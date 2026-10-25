"""Read-only taxonomy search and concept detail, over a dataset a past run
loaded. Nothing here keeps a dataset loaded between requests - no such cache
exists anywhere in this service yet - so both endpoints re-read `dataset`
from disk fresh on each call, via the same `config_json` already saved for
that run by `db.save_run`, reusing `pipeline.load_dataset` rather than adding
a second way to load one. A result reflects the source files' current
contents, not a snapshot of what the run originally saw: editing a dataset
file changes what the same `run_id` returns immediately, with no new run
needed - the run is only how `dataset` is resolved to a set of file paths.
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..config import PipelineConfig
from ..matching.names import normalize
from ..models import ConceptLevel, TaxonomyConceptDetail, TaxonomyConceptRef, TaxonomyConceptSummary, TaxonomyPath
from ..taxonomy import load_dataset
from ..taxonomy.base import TaxonomyDataset

LEVELS: tuple[ConceptLevel, ...] = ("services", "categories", "specialities", "tags")
DEFAULT_SEARCH_LIMIT = 50
MAX_SEARCH_LIMIT = 200


class NotFound(Exception):
    """A run, dataset or concept the caller asked for doesn't exist. The
    route layer turns this into a 404; this module stays framework-agnostic.
    """


def resolve_dataset(run_id: str, dataset_name: str) -> TaxonomyDataset:
    record = db.get_run(run_id)
    if record is None:
        raise NotFound(f"run not found: {run_id}")
    config = PipelineConfig.model_validate_json(record.config_json)
    ds_config = next((d for d in config.datasets if d.name == dataset_name), None)
    if ds_config is None:
        raise NotFound(f"dataset {dataset_name!r} not loaded by run {run_id}")
    # Load only the requested dataset - the run's config may list several
    # (e.g. both taxonomies plus ssic/ssoc reference workbooks), and this
    # lookup has no reason to read, parse, or depend on the rest of them.
    dataset = load_dataset(ds_config)
    if not isinstance(dataset, TaxonomyDataset):
        # A classification reference (ssic/ssoc) - not something this
        # endpoint can search; same "wrong type" idea as
        # Metric.require_datasets, just surfaced as 404 over HTTP instead.
        raise NotFound(f"dataset {dataset_name!r} is not a taxonomy dataset")
    return dataset


def _ref(row: dict[str, Any] | None) -> TaxonomyConceptRef | None:
    if row is None:
        return None
    return TaxonomyConceptRef(id=row.get("id"), name=str(row.get("name")))


def search(
    dataset: TaxonomyDataset, *, q: str | None, level: ConceptLevel | None, limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[TaxonomyConceptSummary]:
    """Name search, case/whitespace/punctuation-insensitive via the same
    `normalize` integrity already uses - one normaliser, not a second one
    that could quietly drift from what `integrity` considers "the same name".
    `q` empty or omitted lists concepts unfiltered (a browse, not a search),
    still capped by `limit` so one call can't dump an entire large dataset.
    """
    levels = (level,) if level else LEVELS
    needle = normalize(q) if q else ""
    results: list[TaxonomyConceptSummary] = []
    for concept_level in levels:
        for row in getattr(dataset, concept_level):
            name = row.get("name")
            if name is None:
                continue
            if needle and needle not in normalize(str(name)):
                continue
            results.append(TaxonomyConceptSummary(id=row.get("id"), name=str(name), level=concept_level))
            if len(results) >= limit:
                return results
    return results


def _tag_paths(dataset: TaxonomyDataset, tag_id: Any) -> list[TaxonomyPath]:
    """Every (service, category, speciality) chain this tag is linked to -
    one per speciality, since a tag can sit under several. A dangling link (no
    such speciality) carries no real path to show - same rows integrity.py
    counts as dangling_links rather than treating as a real relationship."""
    return [
        TaxonomyPath(service=_ref(path.service), category=_ref(path.category), speciality=_ref(path.speciality))
        for path in dataset.hierarchy().tag_paths(tag_id)
    ]


def detail(dataset: TaxonomyDataset, level: ConceptLevel, concept_id: str) -> TaxonomyConceptDetail:
    rows = getattr(dataset, level)
    row = next((r for r in rows if str(r.get("id")) == concept_id), None)
    if row is None:
        raise NotFound(f"no {level} with id {concept_id!r} in dataset {dataset.name!r}")
    name = str(row.get("name"))

    if level == "tags":
        return TaxonomyConceptDetail(
            id=row.get("id"), name=name, level=level, dataset=dataset.name,
            paths=_tag_paths(dataset, row.get("id")),
        )

    hierarchy = dataset.hierarchy()
    if level == "services":
        path = TaxonomyPath(service=_ref(row))
        children = [_ref(c) for c in dataset.categories if c.get("service_id") == row.get("id")]
        tags: list[TaxonomyConceptRef] = []
    elif level == "categories":
        path = TaxonomyPath(service=_ref(hierarchy.service_of(row)), category=_ref(row))
        children = [_ref(s) for s in dataset.specialities if s.get("category_id") == row.get("id")]
        tags = []
    else:  # specialities
        category = hierarchy.category_of(row)
        path = TaxonomyPath(service=_ref(hierarchy.service_of(category)), category=_ref(category), speciality=_ref(row))
        speciality_id = row.get("id")
        tag_ids = {link.get("tag_id") for link in dataset.speciality_tags if link.get("speciality_id") == speciality_id}
        children = []
        tags = [_ref(t) for t in dataset.tags if t.get("id") in tag_ids]

    return TaxonomyConceptDetail(
        id=row.get("id"), name=name, level=level, dataset=dataset.name,
        paths=[path], children=[c for c in children if c is not None], tags=[t for t in tags if t is not None],
    )
