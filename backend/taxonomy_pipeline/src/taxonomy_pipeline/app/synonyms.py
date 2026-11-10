"""Synonym use cases shared by the CLI (`synonym-add`, `synonym-remove`, `synonyms`) and `/synonyms`.

Adding a synonym first checks the concept exists in the named taxonomy, found either
through a config file (the CLI) or through a past run's config (the API, the same
way `/taxonomy` resolves a dataset). The synonym records that taxonomy's snapshot
fingerprint, so its audit event identifies exactly which version of the concept it
was checked against. Neither path writes to a taxonomy file.

Finding the taxonomy fails one of two ways, kept apart so a broken setup isn't reported
as a missing concept: UnknownConcept (no such run, dataset or concept) and
TaxonomyUnavailable (the config can't be read, or the dataset's files can't be loaded).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..config import load_config
from ..enrichment.resolution import taxonomy_snapshot_id
from ..synonyms import (
    Synonym,
    SynonymCreate,
    SynonymList,
    SynonymQuery,
    TaxonomyUnavailable,
    UnknownConcept,
    concept_name,
)
from ..synonyms import add_synonym as store_add
from ..synonyms import list_synonyms as store_list
from ..synonyms import remove_synonym as store_remove
from ..taxonomy import load_dataset
from ..taxonomy.base import TaxonomyDataset
from . import browse


def add_synonym(request: SynonymCreate, *, config_path: str | Path | None = None, run_id: str | None = None) -> Synonym:
    """Approve `request.term` as a synonym of one existing concept and log it.

    Give exactly one of `config_path` (a pipeline config declaring `request.dataset`)
    or `run_id` (a past run that loaded it). Raises UnknownConcept when the run, dataset
    or concept doesn't exist, TaxonomyUnavailable when it can't be loaded, RetiredConcept
    for a retired concept, and SynonymError for a refused term.
    """
    taxonomy = _taxonomy(request.dataset, config_path=config_path, run_id=run_id)
    name = concept_name(taxonomy, request.concept_level, request.concept_id)
    return store_add(request, canonical_name=name, snapshot_id=taxonomy_snapshot_id(taxonomy))


def remove_synonym(synonym_id: str, *, reason: str, actor: str = "unspecified") -> Synonym:
    """Mark one approved synonym removed and log why. The store refuses a blank reason or actor, and an
    unknown (or blank) ID is SynonymNotFound."""
    return store_remove(synonym_id, reason=reason, actor=actor)


def list_synonyms(query: SynonymQuery) -> SynonymList:
    """One page of synonyms matching the query's filters, without loading any taxonomy."""
    return store_list(query)


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
