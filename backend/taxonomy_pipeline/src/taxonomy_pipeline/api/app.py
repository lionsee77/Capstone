"""FastAPI app: a thin HTTP layer over `pipeline.run_pipeline`.

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

from .. import db
from ..config import PipelineConfig
from ..models import ConceptLevel, RunReport, TaxonomyConceptDetail, TaxonomyConceptSummary
from ..pipeline import run_pipeline
from ..quality.registry import METRICS
from . import taxonomy as taxonomy_api
from .proposals import router as proposals_router
from .security import disallowed_paths, require_token

app = FastAPI(
    title="Taxonomy Validation Pipeline",
    version="0.1.0",
    description="Runs integrity/breadth/depth/saturation checks against one or more taxonomy datasets.",
    dependencies=[Depends(require_token)],
)
app.include_router(proposals_router)


def _execute_and_save(queued: RunReport, config: PipelineConfig) -> None:
    config_json = config.model_dump_json()
    # Persist "running" before the (potentially slow) pipeline starts, so a
    # poller sees queued -> running -> done|failed rather than a jump.
    queued.status = "running"
    db.save_run(queued, config_json=config_json)

    report = run_pipeline(config)
    report.run_id = queued.run_id  # keep the id the caller was given, not a fresh one
    db.save_run(report, config_json=config_json)


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
def list_metrics() -> dict:
    """Self-describing metric catalogue, so a dashboard can build a
    "pick tests to run" UI without hardcoding anything."""
    return {
        metric_id: {"description": metric.description, "default_params": metric.params_schema()}
        for metric_id, metric in METRICS.items()
    }


@app.post("/runs", status_code=202)
def create_run(config: PipelineConfig, background_tasks: BackgroundTasks) -> dict:
    _require_absolute_dataset_paths(config)
    _require_allowed_dataset_paths(config)
    _require_no_judge(config)
    report = RunReport(status="queued")
    db.save_run(report, config_json=config.model_dump_json())
    background_tasks.add_task(_execute_and_save, report, config)
    return {"run_id": report.run_id, "status": "queued"}


@app.get("/runs")
def list_runs(limit: int = 50) -> list[dict]:
    return [
        {"run_id": r.run_id, "status": r.status, "created_at": r.created_at}
        for r in db.list_runs(limit=limit)
    ]


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> RunReport:
    record = db.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunReport.model_validate_json(record.report_json)


@app.get("/taxonomy/search")
def search_taxonomy(
    run_id: str, dataset: str, q: str | None = None, level: ConceptLevel | None = None, limit: int = 50,
) -> list[TaxonomyConceptSummary]:
    """Name search over a dataset loaded by a past run - re-resolved from
    that run's own config each call; see `api/taxonomy.py` for why."""
    try:
        resolved = taxonomy_api.resolve_dataset(run_id, dataset)
    except taxonomy_api.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    limit = max(1, min(limit, taxonomy_api.MAX_SEARCH_LIMIT))
    return taxonomy_api.search(resolved, q=q, level=level, limit=limit)


@app.get("/taxonomy/{level}/{concept_id}")
def get_taxonomy_concept(level: ConceptLevel, concept_id: str, run_id: str, dataset: str) -> TaxonomyConceptDetail:
    """One concept's full path(s) up, its children, and its tags."""
    try:
        resolved = taxonomy_api.resolve_dataset(run_id, dataset)
        return taxonomy_api.detail(resolved, level, concept_id)
    except taxonomy_api.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
