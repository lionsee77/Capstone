"""The plug-in point for taxonomy data sources.

Add a new source (a live database, a CSV export, ...) by implementing
`TaxonomySource.load()` and registering a builder for it in
`datasets/__init__.py`. Nothing else in the pipeline needs to change - every
metric only ever sees the canonical `TaxonomyDataset` shape below.
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

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


class TaxonomySource(Protocol):
    """A loader that can produce one named TaxonomyDataset."""

    def load(self) -> TaxonomyDataset: ...
