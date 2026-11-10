"""Synonym use cases shared by the CLI (`synonym-add`, `synonym-remove`, `synonyms`, `synonym-check`,
`synonym-resolve`, `synonym-suggest`, `synonym-review`) and the `/synonyms` routes.

Adding a synonym first checks the concept exists in the named taxonomy, found either
through a config file (the CLI) or through a past run's config (the API, the same
way `/taxonomy` resolves a dataset), then that the term won't become ambiguous
(`SynonymIndex.check_new`, #61). The synonym records that taxonomy's snapshot
fingerprint, so its audit event identifies exactly which version of the concept it
was checked against. Neither path writes to a taxonomy file.

Finding the taxonomy fails one of two ways, kept apart so a broken setup isn't reported
as a missing concept: UnknownConcept (no such run, dataset or concept) and
TaxonomyUnavailable (the config can't be read, or the dataset's files can't be loaded).

`resolve_term` answers "what does this term mean here?" from the live approved
synonyms (#60). `export_synonyms` freezes them into a `synonym_set` file, which is
what a scoring config names: scores must not move when a reviewer approves a synonym.

`suggest_synonyms` runs one source (`synonyms/sources/`) and stores what it suggests as
`proposed`; `review_synonym` approves (with the same checks as adding by hand) or
rejects one. Nothing a source suggests resolves until a person approves it.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from ..config import load_config
from ..enrichment.resolution import taxonomy_snapshot_id
from ..synonyms import (
    InvalidScope,
    Resolution,
    SuggestionReport,
    Synonym,
    SynonymCreate,
    SynonymError,
    SynonymIndex,
    SynonymLevel,
    SynonymList,
    SynonymNotFound,
    SynonymProblem,
    SynonymQuery,
    SynonymSet,
    TaxonomyUnavailable,
    UnknownConcept,
    approved_synonyms,
    concept_name,
    get_synonym,
    propose_synonyms,
)
from ..synonyms import add_synonym as store_add
from ..synonyms import list_synonyms as store_list
from ..synonyms import remove_synonym as store_remove
from ..synonyms import review_synonym as store_review
from ..synonyms.sources import get_source
from ..taxonomy import load_dataset
from ..taxonomy.base import TaxonomyDataset
from . import browse


def add_synonym(request: SynonymCreate, *, config_path: str | Path | None = None, run_id: str | None = None) -> Synonym:
    """Approve `request.term` as a synonym of one existing concept and log it.

    Give exactly one of `config_path` (a pipeline config declaring `request.dataset`)
    or `run_id` (a past run that loaded it). Raises UnknownConcept when the run, dataset
    or concept doesn't exist, TaxonomyUnavailable when it can't be loaded, RetiredConcept
    for a retired concept, ConflictingSynonym when the term already means another concept
    where it would apply, InvalidScope for a scope the concept isn't in, and SynonymError
    for any other refused term.
    """
    taxonomy = _taxonomy(request.dataset, config_path=config_path, run_id=run_id)
    name = concept_name(taxonomy, request.concept_level, request.concept_id)
    index = SynonymIndex(taxonomy)  # built before the store takes its write lock

    def check(same_term: list[Synonym]) -> None:
        index.check_new(request, same_term)

    return store_add(request, canonical_name=name, snapshot_id=taxonomy_snapshot_id(taxonomy), check=check)


def remove_synonym(synonym_id: str, *, reason: str, actor: str = "unspecified") -> Synonym:
    """Mark one approved synonym removed and log why. The store refuses a blank reason or actor, and an
    unknown (or blank) ID is SynonymNotFound."""
    return store_remove(synonym_id, reason=reason, actor=actor)


def list_synonyms(query: SynonymQuery) -> SynonymList:
    """One page of synonyms matching the query's filters, without loading any taxonomy."""
    return store_list(query)


def synonym_problems(dataset: str, *, config_path: str | Path | None = None,
                     run_id: str | None = None) -> list[SynonymProblem]:
    """`dataset`'s approved synonyms that don't resolve as recorded against its taxonomy (found as for
    `add_synonym`): stale ones (concept gone, renamed or moved out of scope), and global synonyms that are
    another concept's name or that two concepts share, e.g. from before the #61 rules existed."""
    taxonomy = _taxonomy(dataset, config_path=config_path, run_id=run_id)
    return SynonymIndex(taxonomy, approved_synonyms(dataset)).problems()


def resolve_term(
    term: str, *, dataset: str, config_path: str | Path | None = None, run_id: str | None = None,
    level: SynonymLevel | None = None, within_level: str | None = None, within_id: str | None = None,
) -> Resolution:
    """What `term` means in `dataset`, through its concept names and approved synonyms.

    `within_level`/`within_id` name the branch the lookup is made in (a category, speciality
    or tag), which is what lets scoped synonyms apply: both or neither, and an unknown
    branch is UnknownConcept. Find the taxonomy as `add_synonym` does.
    """
    if not term.strip():
        raise ValueError("term must not be blank")
    if (within_level is None) != (within_id is None):
        raise ValueError("give both within_level and within_id, or neither")
    taxonomy = _taxonomy(dataset, config_path=config_path, run_id=run_id)
    index = SynonymIndex(taxonomy, approved_synonyms(dataset, term=term))
    within = (within_level, within_id) if within_level is not None and within_id is not None else None
    try:
        return index.resolve(term, level=level, within=within)
    except InvalidScope as exc:
        raise UnknownConcept(str(exc)) from exc


def export_synonyms(dataset: str, path: str | Path) -> SynonymSet:
    """Write `dataset`'s approved synonyms to a new `synonym_set` file; an existing file is never overwritten.

    A scoring config pins the file, so rewriting it in place would silently change what
    that config scores: export to a new name (e.g. `synonyms_v2.json`) instead. A dataset
    with no approved synonyms (a typo in its name, say) is refused rather than written as
    an empty set that a config could pin.
    """
    path = Path(path)
    if path.exists():
        raise ValueError(f"{path} already exists; export to a new file so configs pinned to it keep their scores")
    synonyms = approved_synonyms(dataset)
    if not synonyms:
        raise ValueError(f"{dataset!r} has no approved synonyms to export; check the dataset name")
    exported = SynonymSet(name=path.stem, dataset=dataset, synonyms=[synonym.mapping() for synonym in synonyms])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(exported.model_dump_json(indent=2))
    return exported


def suggest_synonyms(
    source_id: str, *, dataset: str, config_path: str | Path | None = None, run_id: str | None = None,
    path: str | Path | None = None, actor: str = "unspecified",
) -> SuggestionReport:
    """Run one synonym source over `dataset` and store its suggestions as `proposed` for review.

    `path` is the downloaded file a source like ESCO or O*NET reads; built-in sources
    (spelling) take none. Suggestions for retired concepts, and ones already proposed,
    approved, rejected or removed, are skipped and counted.
    """
    source = get_source(source_id)
    if not actor.strip():
        raise ValueError("actor must not be blank")
    if source.needs_file and path is None:
        raise ValueError(f"the {source.id} source needs a file to read")
    if not source.needs_file and path is not None:
        raise ValueError(f"the {source.id} source doesn't read a file")
    taxonomy = _taxonomy(dataset, config_path=config_path, run_id=run_id)
    requests, skipped = [], Counter()
    for suggestion in source.suggest(taxonomy, Path(path) if path is not None else None):
        try:
            name = concept_name(taxonomy, suggestion.concept_level, suggestion.concept_id)
        except SynonymError:  # unknown or retired concept
            skipped["retired_or_unknown_concept"] += 1
            continue
        requests.append((SynonymCreate(
            dataset=dataset, concept_level=suggestion.concept_level, concept_id=suggestion.concept_id,
            term=suggestion.term, reason=suggestion.evidence, actor=actor, source=source.id,
        ), name))
    proposed, refused = propose_synonyms(dataset, requests, snapshot_id=taxonomy_snapshot_id(taxonomy))
    return SuggestionReport(source=source.id, dataset=dataset, proposed=proposed, skipped=dict(skipped + refused))


def review_synonym(
    synonym_id: str, *, approve: bool, reason: str, actor: str = "unspecified",
    config_path: str | Path | None = None, run_id: str | None = None,
) -> Synonym:
    """Approve or reject one proposed synonym; rejecting needs no taxonomy.

    Approving checks it against the taxonomy (found as for `add_synonym`) exactly like
    adding it by hand: the concept must still exist and be active, the term may not be
    its current name, and it may not already mean another concept. The approved row and
    its audit event record the concept's current name and snapshot, not the proposal's.
    """
    synonym = get_synonym(synonym_id)
    if synonym is None or synonym.status != "proposed":
        raise SynonymNotFound(f"no proposed synonym with ID {synonym_id!r}")
    if not approve:
        return store_review(synonym.synonym_id, approve=False, reason=reason, actor=actor)
    taxonomy = _taxonomy(synonym.dataset, config_path=config_path, run_id=run_id)
    name = concept_name(taxonomy, synonym.concept_level, synonym.concept_id)
    request = SynonymCreate(dataset=synonym.dataset, concept_level=synonym.concept_level,
                            concept_id=synonym.concept_id, term=synonym.term, reason=reason, actor=actor)
    index = SynonymIndex(taxonomy)  # built before the store takes its write lock

    def check(same_term: list[Synonym]) -> None:
        index.check_new(request, same_term)

    return store_review(synonym.synonym_id, approve=True, reason=reason, actor=actor, canonical_name=name,
                        snapshot_id=taxonomy_snapshot_id(taxonomy), check=check)


def _taxonomy(dataset: str, *, config_path: str | Path | None, run_id: str | None) -> TaxonomyDataset:
    if (config_path is None) == (run_id is None):
        raise ValueError("give exactly one of a config path or a run ID to find the taxonomy")
    if run_id is not None:
        try:
            return browse.resolve_dataset(run_id, dataset)
        except browse.NotFound as exc:
            raise UnknownConcept(str(exc)) from exc
        except (OSError, ValueError) as exc:  # the run's config points at a file that moved or no longer parses
            raise TaxonomyUnavailable(
                f"run {run_id}'s dataset {dataset!r} couldn't be loaded: {type(exc).__name__}: {exc}"
            ) from exc
    try:
        config = load_config(config_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise TaxonomyUnavailable(f"config {config_path} couldn't be read: {type(exc).__name__}: {exc}") from exc
    declared = next((item for item in config.datasets if item.name == dataset), None)
    if declared is None:
        raise UnknownConcept(f"{config_path} declares no dataset named {dataset!r}")
    try:
        taxonomy = load_dataset(declared)
    except (OSError, ValueError) as exc:
        raise TaxonomyUnavailable(f"dataset {dataset!r} couldn't be loaded: {type(exc).__name__}: {exc}") from exc
    if not isinstance(taxonomy, TaxonomyDataset):
        raise UnknownConcept(f"{dataset!r} is not a taxonomy dataset")
    return taxonomy
