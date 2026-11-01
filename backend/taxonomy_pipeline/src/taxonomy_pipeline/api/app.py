"""FastAPI app: a thin HTTP layer over the `app/` use cases (`app.scoring` for metrics and runs,
`app.comparison` for comparing two runs, `app.browse` for taxonomy search, `app.enrichment` for proposals).

A run is a batch job, not a request/response - `POST /runs` validates the
config, schedules the work as a background task, and returns immediately
with a run id. Poll `GET /runs/{run_id}` for status (queued -> running ->
done|failed) and, once done, the report. This is also the seam a future admin dashboard talks to.

Every route requires the shared API token, and runs may only read dataset
files under the configured data roots; see `security.py`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException

from ..app import browse, comparison, scoring
from ..config import PipelineConfig
from ..models import (
    ConceptLevel,
    MetricInfo,
    RunComparison,
    RunCreated,
    RunReport,
    RunSummary,
    TaxonomyConceptDetail,
    TaxonomyConceptSummary,
)
from .proposals import router as proposals_router
from .security import disallowed_paths, require_token

app = FastAPI(
    title="Taxonomy Validation Pipeline",
    version="0.1.0",
    description="Runs integrity/breadth/depth/saturation checks against one or more taxonomy datasets.",
    dependencies=[Depends(require_token)],
)
app.include_router(proposals_router)


def _require_absolute_dataset_paths(config: PipelineConfig) -> None:
    """Reject API paths without a config-file directory to resolve them from."""
    relative_paths = [
        {"dataset": dataset.name, "path": path}
        for dataset in config.datasets
        for path in dataset.paths
        if not Path(path).is_absolute()
    ]
    if relative_paths:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "API dataset paths must be absolute server-side paths.",
                "relative_paths": relative_paths,
            },
        )


def _require_no_judge(config: PipelineConfig) -> None:
    """Reject LLM judge settings: a judged run spends OpenAI credits and reads/writes its cache file
    wherever the config points, outside the data-root check. Judged scoring runs from the CLI."""
    judged = [
        {"metric_index": index, "metric_id": metric.id}
        for index, metric in enumerate(config.metrics)
        if metric.params.get("judge") is not None
    ]
    if judged:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "The LLM judge can't be enabled through the API: it spends OpenAI credits and "
                           "writes its cache file on the server. Run judged scoring from the CLI.",
                "judged_metrics": judged,
            },
        )


def _require_allowed_dataset_paths(config: PipelineConfig) -> None:
    """Reject paths outside the data roots, so a run can't be used to read arbitrary server files."""
    outside = disallowed_paths(config)
    if outside:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "API dataset paths must be inside the server's allowed data directories.",
                "disallowed_paths": outside,
            },
        )


@app.get("/metrics")
def list_metrics() -> dict[str, MetricInfo]:
    """Self-describing metric catalogue, so a dashboard can build a
    "pick tests to run" UI without hardcoding anything."""
    return scoring.metric_catalogue()


@app.post("/runs", status_code=202)
def create_run(config: PipelineConfig, background_tasks: BackgroundTasks) -> RunCreated:
    _require_absolute_dataset_paths(config)
    _require_allowed_dataset_paths(config)
    _require_no_judge(config)
    report = scoring.queue_run(config)
    background_tasks.add_task(scoring.execute_queued_run, report, config)
    return RunCreated(run_id=report.run_id)


@app.get("/runs")
def list_runs(limit: int = 50) -> list[RunSummary]:
    return scoring.list_runs(limit=limit)


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> RunReport:
    report = scoring.get_run(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return report


@app.get("/runs/{before_id}/compare/{after_id}")
def compare_runs(
    before_id: str, after_id: str, before_subject: str | None = None, after_subject: str | None = None,
) -> RunComparison:
    """Compare two stored runs metric by metric (#69): values, changes and verdicts, embedding the
    comparability check. Runs come from run history only - never a file path - so a request can't
    read reports elsewhere on the server. Read `comparability.comparable` before any verdict."""
    reports = {label: scoring.get_run(run_id) for label, run_id in (("before", before_id), ("after", after_id))}
    missing = [label for label, report in reports.items() if report is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"{' and '.join(missing)} run not found")
    try:
        return comparison.compare(
            reports["before"], reports["after"], before_subject=before_subject, after_subject=after_subject,
        )
    except ValueError as exc:  # a run scoring several taxonomies needs its subject named
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/taxonomy/search")
def search_taxonomy(
    run_id: str, dataset: str, q: str | None = None, level: ConceptLevel | None = None, limit: int = 50,
) -> list[TaxonomyConceptSummary]:
    """Name search over a dataset loaded by a past run - re-resolved from
    that run's own config each call; see `app/browse.py` for why."""
    try:
        return browse.search_run_dataset(run_id, dataset, q=q, level=level, limit=limit)
    except browse.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/taxonomy/{level}/{concept_id}")
def get_taxonomy_concept(level: ConceptLevel, concept_id: str, run_id: str, dataset: str) -> TaxonomyConceptDetail:
    """One concept's full path(s) up, its children, and its tags."""
    try:
        return browse.concept_detail(run_id, dataset, level, concept_id)
    except browse.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
