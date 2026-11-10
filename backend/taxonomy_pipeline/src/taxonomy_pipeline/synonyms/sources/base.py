"""The plug-in point for synonym sources: anything that can *suggest* other names for concepts.

A source never approves anything. `taxonomy-pipeline synonym-suggest --source <id>`
stores what it returns as `proposed` synonyms for a reviewer to approve or reject
(`synonym-review`), so a wrong suggestion (a general thesaurus would map "Python" to a
snake) costs a click, not a wrong match. Add a source by subclassing `SynonymSource` in
its own module and adding one line to `registry.py`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ...taxonomy.base import TaxonomyDataset
from ..models import SynonymLevel


class Suggestion(BaseModel):
    """One suggested synonym: `term` for the concept at `concept_level`/`concept_id`, and why."""

    model_config = ConfigDict(frozen=True)

    concept_level: SynonymLevel
    concept_id: str
    term: str
    evidence: str


class SynonymSource(ABC):
    id: str
    description: str
    # Sources that read a downloaded file (ESCO, O*NET) need its path; built-in rules don't.
    needs_file: bool = False

    @abstractmethod
    def suggest(self, taxonomy: TaxonomyDataset, path: Path | None = None) -> list[Suggestion]: ...
