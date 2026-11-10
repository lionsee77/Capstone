"""Synonym mappings (PB07): other names for existing canonical concepts, never new taxonomy rows.

Sits between `quality` and `matching` in the context order (`app -> enrichment ->
quality -> synonyms -> matching -> taxonomy`), and writes its history through the
independent `audit` context. Matching code doesn't import this context; when lookups
resolve through synonyms (#60), the approved mappings are passed down to it as data.
"""
from __future__ import annotations

from .models import (
    Resolution,
    ResolvedConcept,
    ScopeLevel,
    Synonym,
    SynonymCreate,
    SynonymLevel,
    SynonymList,
    SynonymQuery,
)
from .resolver import ConflictingSynonym, InvalidScope, SynonymIndex, SynonymProblem
from .store import (
    DuplicateSynonym,
    RetiredConcept,
    SynonymError,
    SynonymNotFound,
    TaxonomyUnavailable,
    UnknownConcept,
    UnsupportedSchema,
    add_synonym,
    approved_synonyms,
    concept_name,
    list_synonyms,
    remove_synonym,
)

__all__ = [
    "ConflictingSynonym", "DuplicateSynonym", "InvalidScope", "Resolution", "ResolvedConcept", "RetiredConcept",
    "ScopeLevel", "Synonym", "SynonymCreate", "SynonymError", "SynonymIndex", "SynonymLevel", "SynonymList",
    "SynonymNotFound", "SynonymProblem", "SynonymQuery", "TaxonomyUnavailable", "UnknownConcept",
    "UnsupportedSchema", "add_synonym", "approved_synonyms", "concept_name", "list_synonyms", "remove_synonym",
]
