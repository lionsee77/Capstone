"""Shared result/report models. These are what the CLI prints, what gets
persisted to SQLite, and what the FastAPI API returns - one shape everywhere.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

NonBlankText = Annotated[str, StringConstraints(pattern=r"\S")]
TaxonomyId = Annotated[int, Field(gt=0)]


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
    # doesn't identify a result. Set by `app.scoring.run_pipeline`; None on reports
    # saved before these fields existed, and on results built outside a run.
    index: int | None = Field(default=None, description="Position of the entry in the config's `metrics` list")
    params: dict[str, Any] | None = Field(
        default=None, description="The entry's params resolved over the metric's defaults",
    )


class DatasetSummary(BaseModel):
    name: str
    counts: dict[str, int]


RunStatus = Literal["queued", "running", "done", "failed"]


class RunReport(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: RunStatus = "queued"
    # The metric definitions (formulas and defaults, see `quality/definitions.py`) the
    # results were scored with. Set by `app.scoring.run_pipeline`; None on reports
    # saved before runs were stamped, so their definitions are unknown rather
    # than assumed current.
    metric_definitions_version: int | None = None
    datasets: list[DatasetSummary] = Field(default_factory=list)
    results: list[MetricResult] = Field(default_factory=list)
    error: str | None = None


class MetricInfo(BaseModel):
    """One `GET /metrics` catalogue entry: what a metric checks and the params it runs with by default."""

    description: str
    default_params: dict[str, Any]


class RunCreated(BaseModel):
    """`POST /runs`: the queued run's id, to poll `GET /runs/{run_id}` with."""

    run_id: str
    status: Literal["queued"] = "queued"


class RunSummary(BaseModel):
    """One `GET /runs` row - a summary, not the full report. `created_at` is the stored ISO timestamp."""

    run_id: str
    status: RunStatus
    created_at: str


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
    # None when a run didn't finish and so records no scored taxonomy; `issues` says why.
    before_subject: str | None
    after_subject: str | None
    before_definitions_version: int | None
    after_definitions_version: int | None
    issues: list[ComparabilityIssue] = Field(default_factory=list)
    metrics: list[MetricComparison] = Field(default_factory=list)

    @computed_field
    @property
    def comparable(self) -> bool:
        return not self.issues and not any(metric.issues for metric in self.metrics)


Direction = Literal["higher", "lower"]
Verdict = Literal["improved", "unchanged", "deteriorated", "changed", "not_compared"]
# How a measure's value reads: a "rate" is a fraction (0.25 shows as 25%, its change in points);
# a "number" is a count or a mean, shown as is.
Unit = Literal["number", "rate"]


class Measure(BaseModel):
    """One headline number read from a metric result, and which way is better.

    Each metric says which of its numbers matter for a version comparison by
    overriding `Metric.measures` (`quality/base.py`); the comparison report (#69)
    only diffs these, never a metric's raw `details`. `better` is None for a
    number whose change is worth showing but isn't good or bad in itself (e.g.
    how many concepts saturation reviewed). `value` is None when the result
    couldn't measure it (e.g. a rate over an empty table). `unit` says how to
    show it, so a report never guesses from the key.
    """

    key: str
    label: str
    value: float | None
    better: Direction | None = None
    unit: Unit = "number"


class MeasureChange(BaseModel):
    """One measure in both runs, the change, and whether it got better.

    `change` and `verdict` are only set for a pair of results scored alike
    (`Comparability`): otherwise the verdict is "not_compared" and the values
    are shown for reference only. "changed" is the verdict for a measure that
    moved but has no better direction.
    """

    key: str
    label: str
    better: Direction | None
    unit: Unit = "number"
    before: float | None
    after: float | None
    change: float | None = None
    relative_change: float | None = Field(default=None, description="change / |before|; None when before is 0")
    verdict: Verdict


class MetricChange(BaseModel):
    """One paired scoring result: its measures, compared, and any reason it doesn't compare."""

    metric_id: str
    label: str = Field(description="Display label, unique within the comparison (see `app.scoring.result_labels`)")
    before_index: int | None = None
    after_index: int | None = None
    issues: list[ComparabilityIssue] = Field(default_factory=list)
    measures: list[MeasureChange] = Field(default_factory=list)


class RunComparison(BaseModel):
    """A version-to-version quality comparison of two runs (PB10, #69): what a reviewer
    reads instead of two raw reports, and what PB11's verdicts (#73) build on.

    Built by `quality.comparison.compare_reports` on top of `check_comparable`,
    which it embeds as `comparability`: no measure gets a change or a verdict
    unless both the runs and its pair of results were scored alike.
    `comparable` also counts a pair whose stored details couldn't be read, an
    issue only the comparison finds, so read it rather than the embedded check's.
    """

    comparability: Comparability
    metrics: list[MetricChange] = Field(default_factory=list)

    @computed_field
    @property
    def comparable(self) -> bool:
        return not self.comparability.issues and not any(metric.issues for metric in self.metrics)


class LineageStage(BaseModel):
    """One taxonomy version in the enrichment lineage (`GET /lineage`): a baseline no round produced
    (a root, `parent` None), or the snapshot one `integrate` round wrote.

    Built by `app.lineage.taxonomy_lineage` from `integrations/*/manifest.json`; see
    `enrichment/lineage.py` for how a round is linked to the snapshot it enriched.
    """

    id: str = Field(description="A root's `baseline:<dataset>`, else the round's integration directory name")
    name: str = Field(description="The root's dataset name, or the round's directory name")
    parent: str | None = None
    sources: list[str] = Field(default_factory=list, description="Incoming datasets the round's tags came from")
    created_at: datetime | None = None
    added: dict[str, int] = Field(default_factory=dict, description="Rows the round added, per table")


class LineageRun(BaseModel):
    """A stored run that scored at least one lineage stage, and under which dataset name it scored each."""

    run_id: str
    created_at: str
    judged: bool = Field(description="Whether any saturation result in the run had the LLM judge on")
    datasets: dict[str, str] = Field(description="Stage id -> the run's dataset name for that stage")


class LineageChain(BaseModel):
    """One root-to-latest path through the lineage, and the runs to read it with, best first.

    A run is listed when it scored at least two of the chain's stages. Best means it covers the
    most stages, then has the judge on, then is newest - so every stage-to-stage comparison in it
    is like for like when one run covers them all.
    """

    stages: list[str]
    runs: list[str] = Field(default_factory=list)


class TaxonomyLineage(BaseModel):
    """`GET /lineage`: every stage, the chains through them (longest first) and the runs that scored them.

    The dashboard reads which runs show a taxonomy's improvement from here, then compares their
    stages through `GET /runs/{before}/compare/{after}`, so it hard-codes no run or stage.
    """

    stages: list[LineageStage] = Field(default_factory=list)
    chains: list[LineageChain] = Field(default_factory=list)
    runs: list[LineageRun] = Field(default_factory=list)


ConceptLevel =Literal["services", "categories", "specialities", "tags"]


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


class LookupMatch(TaxonomyConceptSummary):
    """One existing concept a looked-up term matched, and how (`matching/lookup.py`).

    `lexical_score` is a 0-100 spelling ratio and `semantic_score` a -1 to 1 cosine,
    present only when meaning matching was requested. Both are uncalibrated review
    evidence (#47). A `synonym` match names the approved synonym the term matched; its
    `lexical_score` is 100 because the term is that synonym, spelled alike (#60).
    `active` is false for a retired row, which is still returned: the term does exist,
    and proposing it again would duplicate that row.
    """

    match_type: Literal["exact", "variant", "lexical", "semantic", "synonym"]
    lexical_score: float
    semantic_score: float | None = None
    synonym: str | None = None
    active: bool = True
    paths: list[TaxonomyPath] = Field(default_factory=list)


class TagLookupResult(BaseModel):
    """`GET /lookup`: whether a proposed term already exists, and in which taxonomy version.

    `exists` means `exact` is non-empty. `snapshot` names the integrated round checked.
    """

    query: str
    snapshot: str
    exists: bool
    exact: list[LookupMatch] = Field(default_factory=list)
    near: list[LookupMatch] = Field(default_factory=list)


class SourceImport(BaseModel):
    """What `import-source` wrote (`app.sources.import_source`), plus anything to check before using it.

    `round_config` is set only when a previous snapshot was given; `warnings`
    lists skipped rows, merged tag spellings, unlabelled code prefixes and
    undescribed tags. The CLI prints it; #117's dashboard route will return it.
    """

    name: str
    sql_path: Path
    context_path: Path
    counts: dict[str, int]
    round_config: Path | None = None
    warnings: list[str] = Field(default_factory=list)


RoundStageId = Literal["import", "score", "case_a", "case_b", "merge", "integrate", "score_enriched"]
RoundStageStatus = Literal["pending", "running", "done", "failed", "skipped"]


class RoundStage(BaseModel):
    """One step of an enrichment round; `summary` and `error` are written for a person to read."""

    id: RoundStageId
    status: RoundStageStatus = "pending"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: str | None = None
    error: str | None = None


class RoundSettings(BaseModel):
    """The round form's switches. Thresholds left unset keep the template's value.

    `judge` and `case_b` spend LLM credits, and need the server to allow them (`app.rounds.llm_status`).
    """

    model_config = ConfigDict(extra="forbid")

    judge: bool = False
    case_b: bool = False
    threshold: float | None = Field(default=None, ge=0, le=100)
    semantic_threshold: float | None = Field(default=None, ge=0, le=1)
    add_candidate_lexical_threshold: float | None = Field(default=None, ge=0, le=100)
    add_candidate_semantic_threshold: float | None = Field(default=None, ge=0, le=1)


class RoundState(BaseModel):
    """A round's saved progress (`app/rounds.py`), and everything a status page shows."""

    round_id: str
    baseline_snapshot: str
    created_at: datetime
    source_name: str | None = None
    settings: RoundSettings = Field(default_factory=RoundSettings)
    # The saved mapping, so the form can show it again. The file names in it are this round's own uploads.
    mapping: dict[str, Any] | None = None
    stages: list[RoundStage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    import_counts: dict[str, int] = Field(default_factory=dict)
    merge_counts: dict[str, int] = Field(default_factory=dict)
    pending_sources: int = 0
    decisions_applied: bool = True
    reviewer: str | None = None
    score_run_id: str | None = None
    enriched_run_id: str | None = None

    @computed_field
    @property
    def status(self) -> Literal["setup", "ready", "running", "failed", "review", "ready_to_integrate", "complete"]:
        """Where the round stands, from its stages: what a person can do next."""
        by_id = {stage.id: stage.status for stage in self.stages}
        statuses = set(by_id.values())
        if "running" in statuses:
            return "running"
        if "failed" in statuses:
            return "failed"
        if by_id.get("score_enriched") == "done":
            return "complete"
        if by_id.get("merge") == "done":
            return "review" if self.pending_sources or not self.decisions_applied else "ready_to_integrate"
        return "ready" if by_id.get("import") == "done" else "setup"


class RoundFile(BaseModel):
    """An uploaded file, read far enough to offer its columns as choices. `error` says why it can't be read."""

    name: str
    size: int
    sheets: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    samples: dict[str, list[str]] = Field(default_factory=dict)
    preview_rows: list[dict[str, str]] = Field(default_factory=list)
    sheet_columns: dict[str, list[str]] = Field(default_factory=dict)
    error: str | None = None


class RoundServiceOption(BaseModel):
    id: int
    name: str


class RoundOptions(BaseModel):
    """What the new-round form offers. `saturation_defaults` are the values thresholds left unset will use."""

    snapshots: list[str] = Field(default_factory=list)
    services: list[RoundServiceOption] = Field(default_factory=list)
    llm_allowed: bool = False
    llm_key_configured: bool = False
    saturation_defaults: dict[str, float] = Field(default_factory=dict)
