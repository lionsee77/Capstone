"""A frozen set of approved synonyms, loaded like any other dataset (`type: synonym_set`).

Live synonyms sit in SQLite and change whenever a reviewer approves one, which is
right for lookups but wrong for scoring: the same config would score differently
next week. A scoring run therefore reads an exported file instead
(`taxonomy-pipeline synonyms --export`), named in the config like a reference
workbook, so the file is pinned with the config and `check-comparable` compares its
row count between two runs, as it does for SSIC and SSOC.

`SynonymMapping` lives here, at the bottom of the context order, because matching
(saturation's `KnownPool`) and screening both read it; the `synonyms` context
builds these from its stored rows.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SynonymMapping(BaseModel):
    """What matching needs from one approved synonym: the term, the concept it means, and its scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    synonym_id: str
    term: str
    normalized_term: str
    term_key: str  # `match_key(term)`: what every stage compares
    concept_level: Literal["categories", "specialities", "tags"]
    concept_id: str
    concept_name: str
    scope_level: Literal["categories", "specialities"] | None = None
    scope_id: str | None = None

    @property
    def scope(self) -> tuple[str, str] | None:
        return (self.scope_level, self.scope_id) if self.scope_level and self.scope_id else None


class SynonymSet(BaseModel):
    """An exported set of one dataset's approved synonyms."""

    name: str
    dataset: str = Field(description="the taxonomy dataset the synonyms' concept IDs belong to")
    synonyms: list[SynonymMapping] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        scoped = sum(1 for synonym in self.synonyms if synonym.scope)
        return {"synonyms": len(self.synonyms), "global": len(self.synonyms) - scoped, "scoped": scoped}


class SynonymSetSource:
    """Reads one exported synonym file (JSON)."""

    def __init__(self, name: str, path: str | Path) -> None:
        self.name, self.path = name, Path(path)

    def load(self) -> SynonymSet:
        exported = SynonymSet.model_validate_json(self.path.read_text(encoding="utf-8"))
        return exported.model_copy(update={"name": self.name})
