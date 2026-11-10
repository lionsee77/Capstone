"""Synonym contracts shared by storage, the app use case, the CLI and the API.

A synonym is another name for one existing canonical concept. It never becomes a
taxonomy row of its own: it lives in its own table and points at the concept by
dataset, level and ID. Concept IDs are strings, as in the audit log, because
they are scoped by dataset rather than being foreign keys into a client table.

Only `approved` and `removed` exist today: a person adding a synonym is the
approval. Suggested synonyms from external sources (ESCO, O*NET, spelling
variants) will add a `proposed` state when that workflow is built (PB07, #25).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..audit.models import Text

SynonymLevel = Literal["categories", "specialities", "tags"]
SynonymStatus = Literal["approved", "removed"]
# The audit log names levels in the singular.
AUDIT_LEVELS: dict[str, str] = {"categories": "category", "specialities": "speciality", "tags": "tag"}


class SynonymCreate(BaseModel):
    """A request to map `term` onto one existing concept, validated before anything is stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: Text
    concept_level: SynonymLevel
    concept_id: Text
    term: Text
    reason: Text
    actor: Text = "unspecified"
    source: Text = "manual"


class Synonym(BaseModel):
    """One stored mapping. `term` and `concept_name` stay separate, so a synonym can always
    be told apart from the canonical name it points at."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    synonym_id: Text
    term: Text
    normalized_term: Text
    # `match_key(term)`: what duplicates and lookups compare, so "Audits" is the same term as "audit" (#59).
    term_key: Text
    dataset: Text
    concept_level: SynonymLevel
    concept_id: Text
    concept_name: Text
    snapshot_id: Text
    status: SynonymStatus
    source: Text
    reason: Text
    actor: Text
    created_at: datetime
    removed_at: datetime | None = None
    removed_by: Text | None = None
    removal_reason: Text | None = None


class SynonymQuery(BaseModel):
    """Filters shared by `taxonomy-pipeline synonyms` and `GET /synonyms`; all are ANDed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: Text | None = None
    concept_level: SynonymLevel | None = None
    concept_id: Text | None = None
    term: Text | None = None
    include_removed: bool = False
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


class SynonymList(BaseModel):
    """One page of synonyms, oldest first."""

    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    synonyms: list[Synonym]
