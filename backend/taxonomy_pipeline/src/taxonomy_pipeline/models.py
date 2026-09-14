"""Shared result/report models. These are what the CLI prints, what gets
persisted to SQLite, and what the FastAPI API returns - one shape everywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


class DatasetSummary(BaseModel):
    name: str
    counts: dict[str, int]


class RunReport(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: Literal["queued", "running", "done", "failed"] = "queued"
    datasets: list[DatasetSummary] = Field(default_factory=list)
    results: list[MetricResult] = Field(default_factory=list)
    error: str | None = None
