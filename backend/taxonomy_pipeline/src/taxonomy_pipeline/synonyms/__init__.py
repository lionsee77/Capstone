"""Synonym mappings (PB07): other names for existing canonical concepts, never new taxonomy rows.

Sits between `quality` and `matching` in the context order (`app -> enrichment ->
quality -> synonyms -> matching -> taxonomy`), and writes its history through the
independent `audit` context. Matching code doesn't import this context; when lookups
resolve through synonyms (#60), the approved mappings are passed down to it as data.
"""
from __future__ import annotations

from .models import Synonym, SynonymCreate, SynonymLevel, SynonymList, SynonymQuery
from .store import (
    DuplicateSynonym,
    SynonymError,
    SynonymNotFound,
    UnknownConcept,
    add_synonym,
    concept_name,
    list_synonyms,
    remove_synonym,
)

__all__ = [
    "DuplicateSynonym", "Synonym", "SynonymCreate", "SynonymError", "SynonymLevel", "SynonymList",
    "SynonymNotFound", "SynonymQuery", "UnknownConcept", "add_synonym", "concept_name", "list_synonyms",
    "remove_synonym",
]
