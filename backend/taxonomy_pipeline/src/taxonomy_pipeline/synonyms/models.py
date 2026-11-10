"""Synonym contracts shared by storage, the app use case, the CLI and the API.

A synonym is another name for one existing canonical concept. It never becomes a
taxonomy row of its own: it lives in its own table and points at the concept by
dataset, level and ID. Concept IDs are strings, as in the audit log, because
they are scoped by dataset rather than being foreign keys into a client table.

A synonym is global, or scoped to one category or speciality branch (#61): a scoped
synonym only resolves for lookups made inside that branch, which is how one word can
mean different things in different parts of the taxonomy ("pitch" in Sales vs. Sports).

Statuses: a person adding a synonym approves it directly (`approved`). A source
(spelling variants, ESCO, O*NET; `synonyms/sources/`) only suggests: its rows start
`proposed`, and a reviewer approves or rejects each one. `rejected` rows are kept so
the same suggestion isn't offered again, and `removed` keeps a retired synonym's
history. Only `approved` synonyms ever resolve.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..audit.models import Text
from ..taxonomy.synonym_set import SynonymMapping

SynonymLevel = Literal["categories", "specialities", "tags"]
# A scope is a branch a concept sits in: a category, or a speciality (for a tag).
ScopeLevel = Literal["categories", "specialities"]
SynonymStatus = Literal["proposed", "approved", "rejected", "removed"]
# The audit log names levels in the singular.
AUDIT_LEVELS: dict[str, str] = {"categories": "category", "specialities": "speciality", "tags": "tag"}


class _Scoped(BaseModel):
    """A scope is both fields or neither; neither means the synonym is global."""

    scope_level: ScopeLevel | None = None
    scope_id: Text | None = None

    @model_validator(mode="after")
    def _scope_is_complete(self) -> Self:
        if (self.scope_level is None) != (self.scope_id is None):
            raise ValueError("give both scope_level and scope_id, or neither for a global synonym")
        return self

    @property
    def scope(self) -> tuple[str, str] | None:
        return (self.scope_level, self.scope_id) if self.scope_level and self.scope_id else None


class SynonymCreate(_Scoped):
    """A request to map `term` onto one existing concept, validated before anything is stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: Text
    concept_level: SynonymLevel
    concept_id: Text
    term: Text
    reason: Text
    actor: Text = "unspecified"
    source: Text = "manual"


class Synonym(_Scoped):
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
    # Who approved or rejected a suggested (proposed) synonym; None for one a person added directly.
    reviewed_at: datetime | None = None
    reviewed_by: Text | None = None
    review_reason: Text | None = None

    def mapping(self) -> SynonymMapping:
        """The part of this synonym matching uses, for screening settings and exported synonym sets."""
        return SynonymMapping.model_validate(self.model_dump(include=set(SynonymMapping.model_fields)))


class SynonymQuery(BaseModel):
    """Filters shared by `taxonomy-pipeline synonyms` and `GET /synonyms`; all are ANDed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: Text | None = None
    concept_level: SynonymLevel | None = None
    concept_id: Text | None = None
    term: Text | None = None
    include_removed: bool = False
    # One status only, e.g. "proposed" for the review queue; overrides include_removed.
    status: SynonymStatus | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


class SuggestionReport(BaseModel):
    """What one run of a synonym source proposed, and how many suggestions it set aside and why."""

    source: str
    dataset: str
    proposed: list[Synonym]
    skipped: dict[str, int] = Field(default_factory=dict)


class SynonymList(BaseModel):
    """One page of synonyms, oldest first."""

    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    synonyms: list[Synonym]


class ResolvedConcept(_Scoped):
    """One concept a term resolved to, and how: by the concept's own name or through a synonym (and its scope)."""

    model_config = ConfigDict(frozen=True)

    concept_level: SynonymLevel
    concept_id: str
    concept_name: str
    via: Literal["name", "synonym"]
    synonym_id: str | None = None
    synonym_term: str | None = None


class Resolution(BaseModel):
    """What one term means. `resolved` has one concept, `ambiguous` several (all listed, none
    chosen), `unknown` none. Approved synonyms only: removed ones never resolve."""

    term: str
    status: Literal["resolved", "ambiguous", "unknown"]
    matches: list[ResolvedConcept] = Field(default_factory=list)
