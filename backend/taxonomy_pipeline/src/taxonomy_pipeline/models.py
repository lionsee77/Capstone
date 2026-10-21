"""Shared result/report models. These are what the CLI prints, what gets
persisted to SQLite, and what the FastAPI API returns - one shape everywhere.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

NonBlankText = Annotated[str, StringConstraints(pattern=r"\S")]
TaxonomyId = Annotated[int, Field(gt=0)]


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


def utcnow() -> datetime:
    return datetime.now(UTC)


class MetricResult(BaseModel):
    """The output of one metric run.

    `status` distinguishes a metric that ran and found nothing ("ok" with an
    empty findings list) from one that couldn't run at all - "skipped" for a
    known, expected gap (e.g. saturation with no text corpus yet), "error"
    for a real problem (bad dataset reference, bad params).
    """

    metric_id: str
    status: Literal["ok", "skipped", "error"] = "ok"
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    # Which config entry produced this result. A config can run the same metric
    # more than once (e.g. integrity once per dataset), so `metric_id` alone
    # doesn't identify a result. Set by `pipeline.run_pipeline`; None on reports
    # saved before these fields existed, and on results built outside a run.
    index: int | None = Field(default=None, description="Position of the entry in the config's `metrics` list")
    params: dict[str, Any] | None = Field(
        default=None, description="The entry's params resolved over the metric's defaults",
    )


class DatasetSummary(BaseModel):
    name: str
    counts: dict[str, int]


class RunReport(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: Literal["queued", "running", "done", "failed"] = "queued"
    # The metric definitions (formulas and defaults, see `definitions.py`) the
    # results were scored with. Set by `pipeline.run_pipeline`; None on reports
    # saved before runs were stamped, so their definitions are unknown rather
    # than assumed current.
    metric_definitions_version: int | None = None
    datasets: list[DatasetSummary] = Field(default_factory=list)
    results: list[MetricResult] = Field(default_factory=list)
    error: str | None = None


class ComparabilityIssue(BaseModel):
    """One reason two runs' numbers would not compare like for like."""

    kind: Literal["run_status", "definitions_version", "missing_result", "result_status", "settings", "reference_data"]
    message: str


class MetricComparison(BaseModel):
    """One scoring result of either run, matched to the other run's result scored
    the same way. An index is None when the other run has no such result."""

    metric_id: str
    before_index: int | None = Field(default=None, description="Position in the before report's `results`")
    after_index: int | None = Field(default=None, description="Position in the after report's `results`")
    issues: list[ComparabilityIssue] = Field(default_factory=list)


class Comparability(BaseModel):
    """Whether two runs scored their taxonomies the same way - the precondition
    for reading their difference as a change in the taxonomy (PB11).

    Built by `definitions.check_comparable`. `issues` concern the whole run (a
    failed run, different metric definitions); each entry in `metrics` pairs
    one scoring result with its counterpart and lists what stops that pair
    comparing. A version-to-version comparison report (#69) should only show
    deltas for pairs without issues, and none at all when `issues` is not empty.
    """

    before_run_id: str
    after_run_id: str
    before_subject: str
    after_subject: str
    before_definitions_version: int | None
    after_definitions_version: int | None
    issues: list[ComparabilityIssue] = Field(default_factory=list)
    metrics: list[MetricComparison] = Field(default_factory=list)

    @computed_field
    @property
    def comparable(self) -> bool:
        return not self.issues and not any(metric.issues for metric in self.metrics)


ConceptLevel = Literal["services", "categories", "specialities", "tags"]


class TaxonomyConceptRef(BaseModel):
    """A lightweight pointer to one concept - enough to link to it, not its detail."""

    id: int | str
    name: str


class TaxonomyConceptSummary(TaxonomyConceptRef):
    """One `GET /taxonomy/search` result row."""

    level: ConceptLevel


class TaxonomyPath(BaseModel):
    """One parent chain from a concept up to its service. Any step can be
    `None` - an orphan row (e.g. a speciality whose category_id doesn't
    resolve) still gets a path, just a shorter one, same as `integrity`
    reports it as an orphan rather than hiding it."""

    service: TaxonomyConceptRef | None = None
    category: TaxonomyConceptRef | None = None
    speciality: TaxonomyConceptRef | None = None


class TaxonomyConceptDetail(BaseModel):
    """One concept with where it sits and what's under it.

    `paths` has exactly one entry for every level except `tags`: a tag can be
    linked to several specialities (each under its own category/service), so
    it gets one path per linked speciality instead of one path picked
    arbitrarily. `children` is the next level down the strict hierarchy
    (services -> categories -> specialities); tags aren't "children" of a
    speciality in that sense; they're `tags`, which stays empty except for
    a speciality's own direct tags - a category's tags are reached by
    following `children` to each speciality instead of flattening them all
    here.
    """

    id: int | str
    name: str
    level: ConceptLevel
    dataset: str
    paths: list[TaxonomyPath] = Field(default_factory=list)
    children: list[TaxonomyConceptRef] = Field(default_factory=list)
    tags: list[TaxonomyConceptRef] = Field(default_factory=list)
