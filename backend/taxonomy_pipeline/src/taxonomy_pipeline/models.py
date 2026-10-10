"""Shared result/report models. These are what the CLI prints, what gets
persisted to SQLite, and what the FastAPI API returns - one shape everywhere.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


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
