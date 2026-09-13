"""FastAPI app: a thin HTTP layer over `pipeline.run_pipeline`.

A run is a batch job, not a request/response - `POST /runs` validates the
config, schedules the work as a background task, and returns immediately
with a run id. Poll `GET /runs/{run_id}` for status and, once done, the
report. This is also the seam a future admin dashboard talks to.
"""
from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, HTTPException

from .. import db
from ..config import PipelineConfig
from ..metrics.registry import METRICS
from ..models import RunReport
from ..pipeline import run_pipeline

app = FastAPI(
    title="Taxonomy Validation Pipeline",
    version="0.1.0",
    description="Runs integrity/breadth/depth/saturation checks against one or more taxonomy datasets.",
)


def _execute_and_save(run_id: str, config: PipelineConfig) -> None:
    report = run_pipeline(config)
    report.run_id = run_id  # keep the id the caller was given, not a fresh one
    db.save_run(report, config_json=config.model_dump_json())


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
    report = RunReport(status="queued")
    db.save_run(report, config_json=config.model_dump_json())
    background_tasks.add_task(_execute_and_save, report.run_id, config)
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
