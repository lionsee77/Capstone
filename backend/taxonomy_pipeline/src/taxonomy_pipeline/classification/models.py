"""Case A/B handoffs and internal Part 6 records, independent of transport and storage.

ClassificationOutput is the producer contract. Temporary legacy intake and shared
report models support validation.py; matching and preview construction remain separate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from ..datasets.base import TaxonomyDataset
from ..models import HierarchyGroup as HierarchyGroup
from ..models import InputLocation as InputLocation
from ..models import MetricResult
from ..models import NonBlankText as NonBlankText
from ..models import SourceTagRef as SourceTagRef
from ..models import TaxonomyId as TaxonomyId
from ..proposals.models import ProposalScreeningReport, ScreenedProposal, ScreeningSettings

Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
CanonicalTagId = TaxonomyId | NonBlankText


class ClassificationOutput(BaseModel):
    """One proposed placement; unresolved records keep the path and confidence null."""

    model_config = ConfigDict(strict=True, extra="ignore", json_schema_extra={
        "oneOf": [
            {"properties": {
                "service": {"type": "string"},
                "category": {"type": "string"}, "speciality": {"type": "string"},
                "confidence": {"type": "number"},
            }},
            {"properties": {
                "service": {"type": "null"},
                "category": {"type": "null"}, "speciality": {"type": "null"},
                "confidence": {"type": "null"},
            }},
        ],
    })

    source_dataset: NonBlankText
    tag_id: NonBlankText
    tag: NonBlankText
    original_classification: Literal["classified", "unclassified"]
    service: Literal["Industry", "Function", "Subject", "Others"] | None
    category: NonBlankText | None
    speciality: NonBlankText | None
    confidence: Confidence | None
    reason: NonBlankText
    # The one optional field. A producer sets it on a placement it can only offer as a
    # candidate (e.g. one of several possible categories), or on any placement of a tag
    # saturation couldn't confirm as new, so resolution holds the tag for a human
    # decision instead of accepting it by default.
    needs_review: bool = Field(default=False, description=(
        "Optional. True when this placement needs a human decision: a candidate the producer could only "
        "propose, or a tag saturation could not confirm as new. The tag stays pending until a reviewer "
        "decides. Omitted when false."
    ))

    @model_validator(mode="after")
    # Require either a complete placement or an explicitly unresolved record.
    def check_placement(self) -> Self:
        values = (self.service, self.category, self.speciality, self.confidence)
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError("service, category, speciality, and confidence must be all present or all null")
        if self.needs_review and self.category is None:
            raise ValueError("needs_review requires a proposed placement for a reviewer to approve")
        return self

    @model_serializer(mode="wrap")
    # Keep ordinary records byte-identical to the nine-field contract.
    def omit_default_review_flag(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if not self.needs_review:
            data.pop("needs_review", None)
        return data


CLASSIFICATION_OUTPUTS = TypeAdapter(list[ClassificationOutput])


class LegacyClassificationOutput(BaseModel):
    """Temporary eight-field intake shape, after producer-specific field mapping."""

    model_config = ConfigDict(strict=True, extra="ignore")

    source_dataset: NonBlankText
    tag_id: NonBlankText
    tag: NonBlankText
    original_classification: Literal["classified", "unclassified"]
    category: NonBlankText | None
    speciality: NonBlankText | None
    confidence: Confidence | None
    reason: NonBlankText

    @model_validator(mode="after")
    # Reject partial legacy placements before applying the temporary service default.
    def check_placement(self) -> Self:
        values = (self.category, self.speciality, self.confidence)
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError("category, speciality, and confidence must be all present or all null")
        return self


class PlacementTarget(BaseModel):
    """Existing IDs; null child IDs mark proposed additions at the output's named path."""

    service_id: TaxonomyId
    category_id: TaxonomyId | None
    speciality_id: TaxonomyId | None

    @model_validator(mode="after")
    # Prevent an existing speciality from being assigned to a category that does not exist yet.
    def check_parent(self) -> Self:
        if self.speciality_id is not None and self.category_id is None:
            raise ValueError("an existing speciality requires an existing category")
        return self


class IntakeRecord(BaseModel):
    """Retain every received row, including rejected and temporarily skipped outputs."""

    origin: InputLocation
    raw: Any
    candidate: dict[str, Any] | None = None
    output: ClassificationOutput | None = None
    target: PlacementTarget | None = None
    status: Literal["valid", "duplicate", "unresolved", "review", "invalid", "skipped"] = "invalid"
    findings: list[str] = Field(default_factory=list)
    duplicate_of: InputLocation | None = None


class BatchValidationReport(BaseModel):
    """Combined intake report with status counts and reviewer-friendly summary metrics."""

    taxonomy: str
    complete: bool
    input_errors: dict[str, str]
    records: list[IntakeRecord]
    counts: dict[str, int]
    metrics: dict[str, int | float]
    merged_outputs: list[ClassificationOutput]


class DuplicateCandidate(BaseModel):
    """A suggested pair for review; a matching score does not approve a merge."""

    model_config = ConfigDict(strict=True, extra="forbid")

    left: SourceTagRef
    right: SourceTagRef
    reason: NonBlankText
    similarity: Confidence | None = None
    match_types: list[Literal["normalized_name", "match_key", "lexical", "semantic"]] = Field(default_factory=list)
    lexical_score: Confidence | None = None
    semantic_score: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)] | None = None
    shared_paths: list[list[str]] = Field(default_factory=list)


class TagProposal(BaseModel):
    """One source identity with all placement evidence, never an automatically merged concept."""

    source: SourceTagRef
    tag: NonBlankText
    original_classification: Literal["classified", "unclassified"]
    placements: list[ClassificationOutput]
    origins: list[InputLocation]


class DeduplicationReport(BaseModel):
    """Candidate evidence and shared hierarchy paths; decisions and preview construction follow later."""

    taxonomy: str
    input_complete: bool
    complete: bool
    skipped_checks: dict[str, str]
    thresholds: dict[str, float]
    counts: dict[str, int]
    omitted_counts: dict[str, int]
    tags: list[TagProposal]
    hierarchy: list[HierarchyGroup]
    candidates: list[DuplicateCandidate]
    screening_hierarchy: list[HierarchyGroup] | None = None


class TaxonomyPath(BaseModel):
    """Approve an existing or proposed speciality without needing a database ID first."""

    model_config = ConfigDict(strict=True, extra="ignore")

    service: Literal["Industry", "Function", "Subject", "Others"]
    category: NonBlankText
    speciality: NonBlankText


class ReviewDecision(BaseModel):
    """One decision format for source-tag groups or a screened hierarchy proposal."""

    model_config = ConfigDict(strict=True, extra="forbid")

    members: list[SourceTagRef] = Field(default_factory=list)
    proposal_id: NonBlankText | None = None
    action: Literal["reuse", "create", "exclude", "defer"]
    reason: NonBlankText
    existing_tag_id: TaxonomyId | None = None
    existing_concept_id: TaxonomyId | None = None
    approved_speciality_ids: list[TaxonomyId] = Field(default_factory=list)
    approved_paths: list[TaxonomyPath] = Field(default_factory=list)
    canonical_name: NonBlankText | None = None

    @model_validator(mode="after")
    # Require an existing target only for reuse and prevent links on excluded or deferred groups.
    def check_action(self) -> Self:
        if self.proposal_id is not None:
            if self.members or self.existing_tag_id is not None or self.approved_paths or self.approved_speciality_ids:
                raise ValueError("A hierarchy decision targets proposal_id only; tag fields are not allowed")
            if self.canonical_name is not None:
                raise ValueError("Hierarchy decisions retain the proposed or existing canonical name")
            if (self.action == "reuse") != (self.existing_concept_id is not None):
                raise ValueError("existing_concept_id is required only for hierarchy reuse")
            return self
        if not self.members or self.existing_concept_id is not None:
            raise ValueError("A tag decision requires members and cannot set existing_concept_id")
        if (self.action == "reuse") != (self.existing_tag_id is not None):
            raise ValueError("existing_tag_id is required only for reuse")
        if self.action in {"exclude", "defer"} and (self.approved_speciality_ids or self.approved_paths):
            raise ValueError("excluded or deferred groups cannot approve speciality links")
        if self.action != "create" and self.canonical_name is not None:
            raise ValueError("canonical_name is allowed only for new tags")
        return self


class ReviewDecisionEvent(BaseModel):
    """Local PB09 handoff: one validated submission, not a taxonomy database mutation."""

    model_config = ConfigDict(strict=True, extra="forbid")

    event_id: NonBlankText
    submission_id: NonBlankText
    recorded_at: datetime
    baseline_snapshot_id: NonBlankText
    event_type: Literal["review_decision"] = "review_decision"
    decision: ReviewDecision


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


class ResolvedHierarchyNode(BaseModel):
    """An approved path node with a baseline ID or stable temporary ID and its parent."""

    level: Literal["category", "speciality"]
    id: CanonicalTagId
    parent_id: CanonicalTagId
    name: NonBlankText
    path: list[str]
    existing: bool


class CanonicalTag(BaseModel):
    """One explicitly accepted concept with every source member preserved."""

    id: CanonicalTagId
    name: NonBlankText
    existing: bool
    members: list[SourceTagRef]


class ResolvedLink(BaseModel):
    """A unique approved speciality/tag pair, including links already in the baseline."""

    speciality_id: CanonicalTagId
    tag_id: CanonicalTagId
    existing: bool


class SourceDisposition(BaseModel):
    """Account for each eligible source tag and placements not approved for linking."""

    source: SourceTagRef
    status: Literal["accepted", "excluded", "deferred", "pending"]
    reason: NonBlankText
    unapproved_paths: list[TaxonomyPath] = Field(default_factory=list)


class ResolutionReport(BaseModel):
    """Resolved additions and reuse for a later preview, retaining complete deduplication evidence."""

    baseline_snapshot_id: NonBlankText
    evidence: DeduplicationReport
    decisions: list[ReviewDecision]
    hierarchy: list[ResolvedHierarchyNode]
    tags: list[CanonicalTag]
    mappings: list[CanonicalMapping]
    links: list[ResolvedLink]
    dispositions: list[SourceDisposition]
    counts: dict[str, int]
    partial: bool
    screening: ProposalScreeningReport | None = None


class ProposalReviewItem(BaseModel):
    """Keep original screening evidence separate from the reviewer's saved choice and resolved node."""

    proposal: ScreenedProposal
    proposed_path: list[str]
    proposed_parent: list[str]
    decision: ReviewDecision | None = None
    resolved_node: ResolvedHierarchyNode | None = Field(
        default=None, description="Saved node allocation; a temporary ID alone does not mean approval or integration.",
    )


class ProposalReviewReport(BaseModel):
    """One read-only response for CLI and API, paginated by proposals but never by their matches."""

    baseline_snapshot_id: str
    settings: ScreeningSettings
    semantic_status: Literal["disabled", "complete", "failed"]
    skipped_checks: dict[str, str]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    proposals: list[ProposalReviewItem]


class PreviewCounts(BaseModel):
    """Separate unique tag and link changes from the number of input records."""

    model_config = ConfigDict(strict=True, extra="forbid")

    added_tags: int = Field(default=0, ge=0)
    reused_tags: int = Field(default=0, ge=0)
    added_links: int = Field(default=0, ge=0)
    omitted_records: int = Field(default=0, ge=0)
    added_categories: int = Field(default=0, ge=0)
    reused_categories: int = Field(default=0, ge=0)
    added_specialities: int = Field(default=0, ge=0)
    reused_specialities: int = Field(default=0, ge=0)
    reused_links: int = Field(default=0, ge=0)
    pending_sources: int = Field(default=0, ge=0)
    deferred_sources: int = Field(default=0, ge=0)
    excluded_sources: int = Field(default=0, ge=0)
    unapproved_placements: int = Field(default=0, ge=0)


class PreviewRow(BaseModel):
    """Flat review rows shared by the CSV exporter and future UI consumers."""

    row_type: Literal["placement", "summary", "omission", "check"] = "placement"
    status: str
    source_dataset: str = ""
    tag_id: str = ""
    tag: str = ""
    original_classification: str = ""
    service: str = ""
    category: str = ""
    speciality: str = ""
    confidence: Confidence | None = None
    reason: str = ""
    canonical_tag_id: CanonicalTagId | None = None
    canonical_tag: str = ""
    category_id: CanonicalTagId | None = None
    speciality_id: CanonicalTagId | None = None
    category_change: str = ""
    speciality_change: str = ""
    tag_change: str = ""
    link_change: str = ""
    review_reason: str = ""
    needs_review: bool = False
    note: str = ""
    count: int | None = None


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
    blocked: bool = False
    rows: list[PreviewRow] = Field(default_factory=list)
    resolution: ResolutionReport | None = None
    baseline_integrity: MetricResult | None = None
    preview_integrity: MetricResult | None = None
