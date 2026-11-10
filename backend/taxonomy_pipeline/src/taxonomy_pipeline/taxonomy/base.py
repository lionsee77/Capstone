"""The plug-in point for taxonomy data sources.

Add a new source (a live database, a CSV export, ...) by implementing
`TaxonomySource.load()` and registering a builder for it in
`taxonomy/__init__.py`. Sources return either the canonical taxonomy shape
or a typed classification reference; metrics validate the types they require.
"""
from __future__ import annotations

from typing import Any, NamedTuple, Protocol

from pydantic import BaseModel, Field

from .references.ssic import SsicReference
from .references.ssoc import SsocReference
from .synonym_set import SynonymSet

CONCEPTS = ("services", "categories", "specialities", "tags", "speciality_tags")


class TaxonomyDataset(BaseModel):
    """One taxonomy, in a canonical shape every metric can rely on regardless
    of which underlying table names or source format it came from.

    Rows are kept as plain dicts (not typed further) since different source
    taxonomies carry different extra columns; every metric only reads the
    handful of keys it actually needs (id, name, category_id, ...).
    """

    name: str
    services: list[dict] = Field(default_factory=list)
    categories: list[dict] = Field(default_factory=list)
    specialities: list[dict] = Field(default_factory=list)
    tags: list[dict] = Field(default_factory=list)
    speciality_tags: list[dict] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {concept: len(getattr(self, concept)) for concept in CONCEPTS}

    def has_table(self, concept: str) -> bool:
        """Whether the source actually provided `concept`, as opposed to it
        defaulting to an empty list. `services=[]` passed explicitly means
        "table present, zero rows" and is still checkable; leaving `services`
        out means the source has no such table. Sources must therefore only
        pass the concepts they really found (see `SqlDumpSource.load`).
        """
        return concept in self.model_fields_set

    def hierarchy(self) -> Hierarchy:
        """Id lookups and parent chains over this dataset's rows, built fresh on each call."""
        return Hierarchy(self)


class TagPath(NamedTuple):
    """One (service, category, speciality) chain a tag sits under; the upper rows are None when missing."""

    service: dict[str, Any] | None
    category: dict[str, Any] | None
    speciality: dict[str, Any]


class Hierarchy:
    """Walks a dataset's parent links: service <- category <- speciality <- tag.

    Saturation, Case A and the `/taxonomy` API all need "which row is this id"
    and "what sits above this row"; this is the one place those maps are built,
    so a source with a different hierarchy shape changes one class. Ids map to
    the last row with that id, and a dangling id resolves to None, as every
    caller expects. Built fresh per call rather than cached on the dataset,
    because rows are plain mutable dicts.
    """

    def __init__(self, dataset: TaxonomyDataset) -> None:
        self.services = {row.get("id"): row for row in dataset.services}
        self.categories = {row.get("id"): row for row in dataset.categories}
        self.specialities = {row.get("id"): row for row in dataset.specialities}
        # Every linked speciality id per tag, in link order, repeats and dangling ids included.
        self.tag_specialities: dict[Any, list[Any]] = {}
        for link in dataset.speciality_tags:
            self.tag_specialities.setdefault(link.get("tag_id"), []).append(link.get("speciality_id"))

    def service_of(self, category: dict[str, Any] | None) -> dict[str, Any] | None:
        return self.services.get(category.get("service_id")) if category else None

    def category_of(self, speciality: dict[str, Any] | None) -> dict[str, Any] | None:
        return self.categories.get(speciality.get("category_id")) if speciality else None

    def service_name(self, service_id: Any) -> Any:
        """The service's name, or None when there is no such service."""
        service = self.services.get(service_id)
        return service.get("name") if service else None

    def parents(self, level: str, row: dict[str, Any]) -> list[dict[str, Any] | None]:
        """The row's direct parents: one service or category, or one speciality per tag link."""
        if level == "categories":
            return [self.services.get(row.get("service_id"))]
        if level == "specialities":
            return [self.categories.get(row.get("category_id"))]
        return [self.specialities.get(sid) for sid in self.tag_specialities.get(row.get("id"), [])]

    def tag_paths(self, tag_id: Any) -> list[TagPath]:
        """One path per distinct speciality the tag links to, in link order; dangling links are skipped."""
        paths = []
        seen: set[Any] = set()
        for speciality_id in self.tag_specialities.get(tag_id, []):
            speciality = self.specialities.get(speciality_id)
            if speciality is None or speciality_id in seen:
                continue
            seen.add(speciality_id)
            category = self.category_of(speciality)
            paths.append(TagPath(self.service_of(category), category, speciality))
        return paths


LoadedDataset = TaxonomyDataset | SsicReference | SsocReference | SynonymSet


class TaxonomySource(Protocol):
    """A loader that produces a taxonomy or classification reference."""

    def load(self) -> LoadedDataset: ...
