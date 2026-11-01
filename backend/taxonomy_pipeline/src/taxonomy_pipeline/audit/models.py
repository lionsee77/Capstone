"""Portable audit contracts shared by storage, governance operations, CLI and API.

Proposal IDs are pipeline references until the client schema arrives. Snapshot
identity scopes numeric concept IDs; proposed concepts use a stable reference.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ChangeAction = Literal[
    "add", "remove", "merge", "synonym_add", "synonym_remove", "deprecate", "reactivate", "review_decision",
]
ChangeLevel = Literal["service", "category", "speciality", "tag", "speciality_tag"]


# Supply UTC timestamps for callers that do not already have a durable operation timestamp.
def utc_now() -> datetime:
    return datetime.now(UTC)


class LinkedReview(BaseModel):
    """Originating review evidence carried by an addition without rewriting earlier review events.

    Legacy resolutions may lack submission artifacts; null actor/time explicitly
    represent unavailable evidence rather than attributing review to the integrator.
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: Text
    decision: dict[str, Any]
    review_event_ids: list[Text] = Field(default_factory=list)
    actor: Text | None = None
    occurred_at: AwareDatetime | None = None


class ChangeEvent(BaseModel):
    """One immutable fact; corrections are new events, never edits of this record."""

    model_config = ConfigDict(extra="forbid")

    event_id: Text = Field(default_factory=lambda: str(uuid4()))
    operation_id: Text
    dataset: Text
    snapshot_id: Text
    concept_level: ChangeLevel
    concept_id: Text | None = None
    concept_ref: Text
    concept_name: Text
    action: ChangeAction
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    reason: Text
    actor: Text = "unspecified"
    occurred_at: datetime = Field(default_factory=utc_now)
    proposal_ids: list[Text] = Field(default_factory=list)
    proposal_namespace: Text = "pipeline"
    decision_ids: list[Text] = Field(default_factory=list)
    decision: dict[str, Any] | None = None
    linked_reviews: list[LinkedReview] = Field(default_factory=list)
    scope: Literal["review", "snapshot", "live"]

    @field_validator("occurred_at")
    @classmethod
    # Require explicit timezone information and store one comparable UTC representation.
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    # Keep review evidence distinct from facts about applied taxonomy changes.
    def check_scope(self) -> Self:
        if (self.action == "review_decision") != (self.scope == "review"):
            raise ValueError("review_decision requires review scope; mutations require snapshot or live scope")
        if self.action == "review_decision" and self.decision is None:
            raise ValueError("review_decision requires the decision payload")
        return self


class ChangeQuery(BaseModel):
    """The same validated filters apply to CLI and HTTP queries."""

    model_config = ConfigDict(extra="forbid")

    dataset: Text | None = None
    snapshot_id: Text | None = None
    concept_level: ChangeLevel | None = None
    concept_id: Text | None = None
    concept_ref: Text | None = None
    action: ChangeAction | None = None
    proposal_id: Text | None = None
    proposal_namespace: Text | None = None
    decision_id: Text | None = None
    since: datetime | None = None
    until: datetime | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("since", "until")
    @classmethod
    # Interpret date filters only when their timezone is explicit.
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("date filters must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    # Reject reversed date ranges rather than silently returning an empty history.
    def check_dates(self) -> Self:
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("since must be before or equal to until")
        return self


class ChangeHistory(BaseModel):
    """A stable, paginated view of history without consulting mutable taxonomy data."""

    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    changes: list[ChangeEvent]
