"""Case A/B handoffs and internal Part 6 records, independent of transport and storage.

Only ClassificationOutput is supplied by producers. These models check data shape;
taxonomy lookup, matching, review processing, and preview construction come later.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from ..datasets.base import TaxonomyDataset

NonBlankText = Annotated[str, StringConstraints(pattern=r"\S")]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
TaxonomyId = Annotated[int, Field(gt=0)]
CanonicalTagId = TaxonomyId | NonBlankText


class ClassificationOutput(BaseModel):
    """One proposed placement; unresolved records keep all three placement values null."""

    model_config = ConfigDict(strict=True, extra="forbid", json_schema_extra={
        "oneOf": [
            {"properties": {
                "category": {"type": "string"}, "speciality": {"type": "string"},
                "confidence": {"type": "number"},
            }},
            {"properties": {
                "category": {"type": "null"}, "speciality": {"type": "null"},
                "confidence": {"type": "null"},
            }},
        ],
    })

    source_dataset: NonBlankText
    tag_id: NonBlankText
    original_tag: NonBlankText
    original_classification: Literal["classified", "unclassified"]
    category: NonBlankText | None
    speciality: NonBlankText | None
    confidence: Confidence | None
    reason: NonBlankText

    @model_validator(mode="after")
    # Require either a complete placement or an explicitly unresolved record.
    def check_placement(self) -> Self:
        values = (self.category, self.speciality, self.confidence)
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError("category, speciality, and confidence must be all present or all null")
        return self


CLASSIFICATION_OUTPUTS = TypeAdapter(list[ClassificationOutput])


class SourceTagRef(BaseModel):
    """Keep equal tag IDs from different datasets distinct."""

    model_config = ConfigDict(strict=True, extra="forbid")

    source_dataset: NonBlankText
    tag_id: NonBlankText


class DuplicateCandidate(BaseModel):
    """A suggested pair for review; a matching score does not approve a merge."""

    model_config = ConfigDict(strict=True, extra="forbid")

    left: SourceTagRef
    right: SourceTagRef
    reason: NonBlankText
    similarity: Confidence | None = None


class ReviewDecision(BaseModel):
    """Group equivalence and approved links are recorded explicitly and separately."""

    model_config = ConfigDict(strict=True, extra="forbid")

    members: list[SourceTagRef] = Field(min_length=1)
    action: Literal["reuse", "create", "exclude", "defer"]
    reason: NonBlankText
    existing_tag_id: TaxonomyId | None = None
    approved_speciality_ids: list[TaxonomyId] = Field(default_factory=list)

    @model_validator(mode="after")
    # Require an existing target only for reuse and prevent links on excluded or deferred groups.
    def check_action(self) -> Self:
        if (self.action == "reuse") != (self.existing_tag_id is not None):
            raise ValueError("existing_tag_id is required only for reuse")
        if self.action in {"exclude", "defer"} and self.approved_speciality_ids:
            raise ValueError("excluded or deferred groups cannot approve speciality links")
        return self


class ValidationFinding(BaseModel):
    """Describe a record or dataset issue without depending on an API error format."""

    model_config = ConfigDict(strict=True, extra="forbid")

    code: NonBlankText
    message: NonBlankText
    source: SourceTagRef | None = None


class ValidationReport(BaseModel):
    """Collect review work; empty findings alone do not imply checks were completed."""

    model_config = ConfigDict(strict=True, extra="forbid")

    complete: bool = False
    findings: list[ValidationFinding] = Field(default_factory=list)
    duplicate_candidates: list[DuplicateCandidate] = Field(default_factory=list)
    unresolved: list[ClassificationOutput] = Field(default_factory=list)


class CanonicalMapping(BaseModel):
    """Map one source tag to a baseline ID or a temporary new-tag ID."""

    model_config = ConfigDict(strict=True, extra="forbid")

    source: SourceTagRef
    canonical_tag_id: CanonicalTagId


class PreviewCounts(BaseModel):
    """Separate unique tag and link changes from the number of input records."""

    model_config = ConfigDict(strict=True, extra="forbid")

    added_tags: int = Field(default=0, ge=0)
    reused_tags: int = Field(default=0, ge=0)
    added_links: int = Field(default=0, ge=0)
    omitted_records: int = Field(default=0, ge=0)


class MergePreview(BaseModel):
    """Serializable preview data; building or accepting a merge is a separate operation."""

    model_config = ConfigDict(strict=True, extra="forbid")

    taxonomy: TaxonomyDataset
    baseline_snapshot_id: NonBlankText
    validation: ValidationReport
    mappings: list[CanonicalMapping] = Field(default_factory=list)
    decisions: list[ReviewDecision] = Field(default_factory=list)
    counts: PreviewCounts = Field(default_factory=PreviewCounts)
    partial: bool = True
