"""The plug-in point for taxonomy data sources.

Add a new source (a live database, a CSV export, ...) by implementing
`TaxonomySource.load()` and registering a builder for it in
`taxonomy/__init__.py`. Sources return either the canonical taxonomy shape
or a typed classification reference; metrics validate the types they require.
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from .references.ssic import SsicReference
from .references.ssoc import SsocReference

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


LoadedDataset = TaxonomyDataset | SsicReference | SsocReference


class TaxonomySource(Protocol):
    """A loader that produces a taxonomy or classification reference."""

    def load(self) -> LoadedDataset: ...
