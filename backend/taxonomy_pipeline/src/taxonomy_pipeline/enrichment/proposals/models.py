"""Persist proposal evidence for resolution replay and the future shared review API.

These models describe hierarchy concepts, not the producer's tag contract.
Names remain evidence; IDs and complete parent paths determine safe reuse.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

Level = Literal["category", "speciality"]
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
CosineScore = Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]


class ExistingConcept(BaseModel):
    """Retain named baseline rows even when a broken parent path prevents reuse."""

    reference: str
    level: Level
    id: int | str | None
    name: str
    path: list[str | None]
    path_ids: list[int | str | None]
    reusable: bool


class ConceptMatch(BaseModel):
    """Scores explain a candidate; they do not establish semantic equivalence."""

    concept: ExistingConcept
    match_types: list[Literal["normalized_name", "match_key", "lexical", "semantic"]]
    lexical_score: Score
    semantic_score: CosineScore | None = None
    same_parent: bool


class ScreenedProposal(BaseModel):
    """One parent-scoped concept with all source references and baseline candidates."""

    proposal_id: str
    level: Level
    name: str
    path: list[str]
    variants: list[list[str]]
    source_tags: list[tuple[str, str]]
    origins: list[tuple[str, int]]
    matches: list[ConceptMatch] = Field(default_factory=list)
    status: Literal["new", "reuse", "review"]
    existing_id: int | None = None
    blocks_creation: bool = False
    reason: str

    @computed_field
    @property
    # Suggest an existing review action without turning screening evidence into reviewer approval.
    def suggested_action(self) -> Literal["create", "reuse", "defer"]:
        return {"new": "create", "reuse": "reuse", "review": "defer"}[self.status]


class ScreeningSettings(BaseModel):
    """Explicit thresholds are persisted so saved reports can be replayed consistently."""

    model_config = ConfigDict(strict=True, extra="forbid")

    lexical_threshold: Score = 0.9
    semantic_threshold: Score = 0.7


class ProposalScreeningReport(BaseModel):
    """Bind all proposed paths and matching coverage to one baseline snapshot."""

    baseline_snapshot_id: str
    settings: ScreeningSettings
    semantic_status: Literal["disabled", "complete", "failed"]
    skipped_checks: dict[str, str] = Field(default_factory=dict)
    proposals: list[ScreenedProposal]
