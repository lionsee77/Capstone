"""Case A/B handoffs and internal Part 6 records, independent of transport and storage.

ClassificationOutput is the producer contract. Shared report models support
validation.py; matching and preview construction remain separate.
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

from ..models import MetricResult
from ..models import NonBlankText as NonBlankText
from ..models import TaxonomyId as TaxonomyId
from ..taxonomy.base import TaxonomyDataset
from .proposals.models import ProposalScreeningReport, ScreenedProposal, ScreeningSettings

Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
CanonicalTagId = TaxonomyId | NonBlankText


class InputLocation(BaseModel):
    """Locate original evidence even after repeated placements are collapsed."""

    input_name: str
    row_index: int


class SourceTagRef(BaseModel):
    """Keep equal tag IDs from different datasets distinct."""

    model_config = ConfigDict(strict=True, extra="forbid")

    source_dataset: NonBlankText
    tag_id: NonBlankText


class HierarchyGroup(BaseModel):
    """Share parent-scoped evidence between classification and proposal screening."""

    level: Literal["category", "speciality"]
    path: list[str]
    variants: list[list[str]]
    existing_id: TaxonomyId | None
    members: list[SourceTagRef]
    origins: list[InputLocation]


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
    """Retain every received row, including rejected outputs. `skipped` only appears in
    merge runs saved while the legacy Case A handoff skip existed."""

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


class PlacementPath(BaseModel):
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
    approved_paths: list[PlacementPath] = Field(default_factory=list)
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
    """Compatible JSONL submission envelope, translated into PB09 events rather than taxonomy mutations."""

    model_config = ConfigDict(strict=True, extra="forbid")

    event_id: NonBlankText
    submission_id: NonBlankText
    recorded_at: datetime
    baseline_snapshot_id: NonBlankText
    actor: NonBlankText = "unspecified"
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


# Why resolution held or flagged a source tag. `reason` says the same thing in words; code branches on these.
ReasonCode = Literal[
    "hierarchy_review",     # hierarchy screening wants a reviewer to look at a proposed path
    "baseline_name_match",  # the tag's normalized name matches an existing baseline tag
    "producer_flagged",     # the producer marked a placement needs_review
    "hierarchy_blocked",    # hierarchy screening blocked a path the decision approved
    "duplicate_candidate",  # deduplication paired the tag with another incoming tag
]


class SourceDisposition(BaseModel):
    """Account for each eligible source tag and placements not approved for linking.

    `reason_codes` lists every reason in `reason`, in the same order; it is empty for
    tags nothing flagged, and for merge runs saved before the codes existed.
    """

    source: SourceTagRef
    status: Literal["accepted", "excluded", "deferred", "pending"]
    reason: NonBlankText
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    unapproved_paths: list[PlacementPath] = Field(default_factory=list)


class MergeRunInfo(BaseModel):
    """A merge run's `run.json`: what `integrate` records as the snapshot's provenance and what
    `bulk-decide` checks the saturation run against (`resolution.json` doesn't carry the run stamp).

    Every field has a default because runs saved before a field existed omit it; unknown keys are
    kept, so a provenance copy loses nothing.
    """

    model_config = ConfigDict(extra="allow")

    saturation_run_ids: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    decisions: int = 0
    baseline_snapshot_id: str | None = None
    blocked: bool = False
    pending_sources: int = 0


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


class ReviewQueuePlacement(BaseModel):
    """One path proposed for a held tag, as a reviewer can approve it."""

    service: str
    category: str
    speciality: str
    confidence: float | None = None
    reason: str


class ReviewQueueMatch(BaseModel):
    """What a held tag resembles in the baseline. `tag_id` is set when it is one baseline tag a reviewer can reuse."""

    score: float
    basis: str
    name: str | None = None
    level: str | None = None
    tag_id: int | None = None


class ReviewQueueDuplicate(BaseModel):
    """Another incoming tag this one may duplicate."""

    source: SourceTagRef
    tag: str
    reason: str
    similarity: float | None = None


class ReviewQueueTag(BaseModel):
    """One tag a reviewer has to, or did, decide: its status, why, and the evidence. `decision` is the saved one."""

    source: SourceTagRef
    tag: str
    status: Literal["accepted", "excluded", "deferred", "pending"]
    reason: str
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    placements: list[ReviewQueuePlacement] = Field(default_factory=list)
    closest: list[ReviewQueueMatch] = Field(default_factory=list)
    duplicates: list[ReviewQueueDuplicate] = Field(default_factory=list)
    decision: ReviewDecision | None = None


class ReviewQueueProposalMatch(BaseModel):
    """An existing category or speciality a proposal resembles; `id` is what a `reuse` decision points at."""

    id: int | str | None
    name: str
    level: str
    path: list[str]
    reusable: bool
    same_parent: bool
    match_types: list[str]
    lexical_score: float
    semantic_score: float | None = None


class ReviewQueueProposal(BaseModel):
    """A proposed category or speciality that needs a decision before tags can be created under it."""

    proposal_id: str
    level: Literal["category", "speciality"]
    name: str
    path: list[str]
    status: Literal["new", "reuse", "review"]
    reason: str
    blocks_creation: bool
    source_tags: int
    matches: list[ReviewQueueProposalMatch] = Field(default_factory=list)
    decision: ReviewDecision | None = None


class ReviewQueueEvidence(BaseModel):
    """Saved after each merge preview, so the queue can be paged without re-reading the merge run's reports."""

    tags: list[ReviewQueueTag] = Field(default_factory=list)
    proposals: list[ReviewQueueProposal] = Field(default_factory=list)


class ReviewQueue(BaseModel):
    """`GET /rounds/{id}/review`: one page of tags, every proposal, and how much of the queue is left."""

    tags: list[ReviewQueueTag] = Field(default_factory=list)
    proposals: list[ReviewQueueProposal] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50
    undecided: int = 0
    decided: int = 0
    proposals_undecided: int = 0


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
