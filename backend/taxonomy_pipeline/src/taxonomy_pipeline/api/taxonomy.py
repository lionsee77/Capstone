"""Read-only taxonomy search and concept detail, over a dataset from a past
run. Nothing here keeps a dataset loaded between requests - no such cache
exists anywhere in this service yet - so both endpoints resolve `dataset`
fresh each call, via the same `config_json` already saved for that run by
`db.save_run`, reusing `pipeline.load_datasets` rather than adding a second
way to load one. A result is only as current as the run it's read from; a
later edit to the source files isn't reflected until a new run loads them.
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..config import PipelineConfig
from ..datasets.base import TaxonomyDataset
from ..metrics.integrity import normalize
from ..models import ConceptLevel, TaxonomyConceptDetail, TaxonomyConceptRef, TaxonomyConceptSummary, TaxonomyPath
from ..pipeline import load_datasets

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
    datasets = load_datasets(config)
    dataset = datasets.get(dataset_name)
    if dataset is None:
        raise NotFound(f"dataset {dataset_name!r} not loaded by run {run_id}")
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
    one per speciality, since a tag can sit under several."""
    specialities_by_id = {row.get("id"): row for row in dataset.specialities}
    categories_by_id = {row.get("id"): row for row in dataset.categories}
    services_by_id = {row.get("id"): row for row in dataset.services}
    paths = []
    seen_speciality_ids: set[Any] = set()
    for link in dataset.speciality_tags:
        if link.get("tag_id") != tag_id:
            continue
        speciality_id = link.get("speciality_id")
        speciality = specialities_by_id.get(speciality_id)
        if speciality is None or speciality_id in seen_speciality_ids:
            # A dangling link (no such speciality) carries no real path to
            # show - same rows integrity.py counts as dangling_links rather
            # than treating as a real relationship.
            continue
        seen_speciality_ids.add(speciality_id)
        category = categories_by_id.get(speciality.get("category_id"))
        service = services_by_id.get(category.get("service_id")) if category else None
        paths.append(TaxonomyPath(service=_ref(service), category=_ref(category), speciality=_ref(speciality)))
    return paths


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

    categories_by_id = {r.get("id"): r for r in dataset.categories}
    services_by_id = {r.get("id"): r for r in dataset.services}
    if level == "services":
        path = TaxonomyPath(service=_ref(row))
        children = [_ref(c) for c in dataset.categories if c.get("service_id") == row.get("id")]
        tags: list[TaxonomyConceptRef] = []
    elif level == "categories":
        service = services_by_id.get(row.get("service_id"))
        path = TaxonomyPath(service=_ref(service), category=_ref(row))
        children = [_ref(s) for s in dataset.specialities if s.get("category_id") == row.get("id")]
        tags = []
    else:  # specialities
        category = categories_by_id.get(row.get("category_id"))
        service = services_by_id.get(category.get("service_id")) if category else None
        path = TaxonomyPath(service=_ref(service), category=_ref(category), speciality=_ref(row))
        speciality_id = row.get("id")
        tag_ids = {link.get("tag_id") for link in dataset.speciality_tags if link.get("speciality_id") == speciality_id}
        children = []
        tags = [_ref(t) for t in dataset.tags if t.get("id") in tag_ids]

    return TaxonomyConceptDetail(
        id=row.get("id"), name=name, level=level, dataset=dataset.name,
        paths=[path], children=[c for c in children if c is not None], tags=[t for t in tags if t is not None],
    )
