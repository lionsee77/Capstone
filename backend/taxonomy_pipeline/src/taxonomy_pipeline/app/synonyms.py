"""Synonym use cases shared by the CLI (`synonym-add`, `synonym-remove`, `synonyms`, `synonym-resolve`)
and the `/synonyms` routes.

Adding a synonym first checks the concept exists in the named taxonomy, found either
through a config file (the CLI) or through a past run's config (the API, the same
way `/taxonomy` resolves a dataset), then that the term won't become ambiguous
(`SynonymIndex.check_new`, #61). The synonym records that taxonomy's snapshot
fingerprint, so its audit event identifies exactly which version of the concept it
was checked against. Neither path writes to a taxonomy file.

`resolve_term` answers "what does this term mean here?" from the live approved
synonyms (#60). `export_synonyms` freezes them into a `synonym_set` file, which is
what a scoring config names: scores must not move when a reviewer approves a synonym.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from ..audit.models import Text
from ..enrichment.resolution import taxonomy_snapshot_id
from ..synonyms import (
    Resolution,
    ScopeLevel,
    Synonym,
    SynonymCreate,
    SynonymIndex,
    SynonymLevel,
    SynonymList,
    SynonymQuery,
    SynonymSet,
    UnknownConcept,
    approved_synonyms,
    concept_name,
)
from ..synonyms import add_synonym as store_add
from ..synonyms import list_synonyms as store_list
from ..synonyms import remove_synonym as store_remove
from ..taxonomy.base import TaxonomyDataset
from . import browse
from .scoring import load_taxonomies


def add_synonym(request: SynonymCreate, *, config_path: str | Path | None = None, run_id: str | None = None) -> Synonym:
    """Approve `request.term` as a synonym of one existing concept and log it.

    Give exactly one of `config_path` (a pipeline config declaring `request.dataset`)
    or `run_id` (a past run that loaded it). Raises UnknownConcept when the dataset
    or concept doesn't exist, ConflictingSynonym when the term already means another
    concept where it would apply, InvalidScope for a scope the concept isn't in, and
    SynonymError for any other refused term.
    """
    taxonomy = _taxonomy(request.dataset, config_path=config_path, run_id=run_id)
    name = concept_name(taxonomy, request.concept_level, request.concept_id)

    def check(approved: list[Synonym]) -> None:
        SynonymIndex(taxonomy, approved).check_new(request)

    return store_add(request, canonical_name=name, snapshot_id=taxonomy_snapshot_id(taxonomy), check=check)


def remove_synonym(synonym_id: str, *, reason: str, actor: str = "unspecified") -> Synonym:
    """Mark one approved synonym removed and log why; reason and actor must not be blank."""
    return store_remove(_text(synonym_id, "synonym ID"), reason=_text(reason, "reason"), actor=_text(actor, "actor"))


def list_synonyms(query: SynonymQuery) -> SynonymList:
    """One page of synonyms matching the query's filters, without loading any taxonomy."""
    return store_list(query)


def resolve_term(
    term: str, *, dataset: str, config_path: str | Path | None = None, run_id: str | None = None,
    level: SynonymLevel | None = None, within: tuple[ScopeLevel, str] | None = None,
) -> Resolution:
    """What `term` means in `dataset`, through its concept names and approved synonyms.

    `within` is the branch the lookup is made in (a category or speciality), which is
    what lets scoped synonyms apply. Find the taxonomy as `add_synonym` does.
    """
    term = _text(term, "term")
    taxonomy = _taxonomy(dataset, config_path=config_path, run_id=run_id)
    index = SynonymIndex(taxonomy, approved_synonyms(dataset))
    if within is not None and within not in index.concepts:
        raise UnknownConcept(f"{dataset} has no {within[0]} with ID {within[1]} to look inside")
    return index.resolve(term, level=level, within=within)


def export_synonyms(dataset: str, path: str | Path) -> SynonymSet:
    """Write `dataset`'s approved synonyms to a new `synonym_set` file; an existing file is never overwritten.

    A scoring config pins the file, so rewriting it in place would silently change what
    that config scores. Export to a new name (e.g. `synonyms_v2.json`) instead.
    """
    path = Path(path)
    if path.exists():
        raise ValueError(f"{path} already exists; export to a new file so configs pinned to it keep their scores")
    exported = SynonymSet(
        name=path.stem, dataset=_text(dataset, "dataset"),
        synonyms=[synonym.mapping() for synonym in approved_synonyms(dataset)],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(exported.model_dump_json(indent=2))
    return exported


def _taxonomy(dataset: str, *, config_path: str | Path | None, run_id: str | None) -> TaxonomyDataset:
    if (config_path is None) == (run_id is None):
        raise ValueError("give exactly one of a config path or a run ID to find the taxonomy")
    if run_id is not None:
        try:
            return browse.resolve_dataset(run_id, dataset)
        except browse.NotFound as exc:
            raise UnknownConcept(str(exc)) from exc
    try:
        taxonomy = load_taxonomies(config_path, dataset)[dataset]
    except ValueError as exc:  # the config declares no taxonomy with that name
        raise UnknownConcept(str(exc)) from exc
    if not isinstance(taxonomy, TaxonomyDataset):  # load_taxonomies already refuses this; narrows the type
        raise UnknownConcept(f"{dataset!r} is not a taxonomy dataset")
    return taxonomy


def _text(value: str, label: str) -> str:
    try:
        return TypeAdapter(Text).validate_python(value)
    except ValidationError as exc:
        raise ValueError(f"{label} must not be blank") from exc
