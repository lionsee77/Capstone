"""Shared result/report models. These are what the CLI prints, what gets
persisted to SQLite, and what the FastAPI API returns - one shape everywhere.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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
