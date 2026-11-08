"""Synonym use cases shared by the CLI (`synonym-add`, `synonym-remove`, `synonyms`) and `/synonyms`.

Adding a synonym first checks the concept exists in the named taxonomy, found either
through a config file (the CLI) or through a past run's config (the API, the same
way `/taxonomy` resolves a dataset). The synonym records that taxonomy's snapshot
fingerprint, so its audit event identifies exactly which version of the concept it
was checked against. Neither path writes to a taxonomy file.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from ..audit.models import Text
from ..enrichment.resolution import taxonomy_snapshot_id
from ..synonyms import (
    Synonym,
    SynonymCreate,
    SynonymList,
    SynonymQuery,
    UnknownConcept,
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
    or concept doesn't exist, and SynonymError for a refused term.
    """
    taxonomy = _taxonomy(request.dataset, config_path=config_path, run_id=run_id)
    name = concept_name(taxonomy, request.concept_level, request.concept_id)
    return store_add(request, canonical_name=name, snapshot_id=taxonomy_snapshot_id(taxonomy))


def remove_synonym(synonym_id: str, *, reason: str, actor: str = "unspecified") -> Synonym:
    """Mark one approved synonym removed and log why; reason and actor must not be blank."""
    return store_remove(_text(synonym_id, "synonym ID"), reason=_text(reason, "reason"), actor=_text(actor, "actor"))


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
