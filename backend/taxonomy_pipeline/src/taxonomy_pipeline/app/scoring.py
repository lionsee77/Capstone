"""Scoring use cases: run a config's metrics, save and load the report, compare two runs, and the
run-history and metric-catalogue reads behind the `/metrics` and `/runs` routes.

Both the CLI (`cli.py`) and the FastAPI app (`api/app.py`) call `run_pipeline`
directly - neither talks to the other over HTTP, so there is exactly one
implementation of "what a run does". The dataset loaders here are shared with
the enrichment use cases (`app/enrichment.py`).
"""
from __future__ import annotations

import gzip
import zlib
from collections import Counter
from pathlib import Path
from typing import Any

from .. import db
from ..config import PipelineConfig, load_config
from ..models import Comparability, DatasetSummary, MetricInfo, MetricResult, RunReport, RunSummary, utcnow
from ..quality.definitions import METRIC_DEFINITIONS_VERSION, check_comparable
from ..quality.registry import METRICS, get_metric
from ..taxonomy import load_dataset
from ..taxonomy.base import LoadedDataset, TaxonomyDataset


def load_datasets(config: PipelineConfig) -> dict[str, LoadedDataset]:
    return {ds_config.name: load_dataset(ds_config) for ds_config in config.datasets}


def load_taxonomies(config_path: str | Path, *names: str) -> dict[str, LoadedDataset]:
    """Load only the named datasets from a config, so unrelated reference workbooks aren't required;
    each must be a taxonomy."""
    config = load_config(config_path)
    config.datasets = [dataset for dataset in config.datasets if dataset.name in names]
    datasets = load_datasets(config)
    for name in names:
        if not isinstance(datasets.get(name), TaxonomyDataset):
            raise ValueError(f"{config_path} declares no taxonomy dataset named {name!r}")
    return datasets


def metric_catalogue() -> dict[str, MetricInfo]:
    """Every registered metric's description and default params, so a dashboard needs no hardcoded list."""
    return {
        metric_id: MetricInfo(description=metric.description, default_params=metric.params_schema())
        for metric_id, metric in METRICS.items()
    }


def queue_run(config: PipelineConfig) -> RunReport:
    """Record a run as queued in run history, so its id can be polled before it starts."""
    report = RunReport(status="queued")
    db.save_run(report, config_json=config.model_dump_json())
    return report


def execute_queued_run(queued: RunReport, config: PipelineConfig) -> RunReport:
    """Run a queued run's config, saving it as running first and as done or failed after."""
    config_json = config.model_dump_json()
    # Persist "running" before the (potentially slow) pipeline starts, so a
    # poller sees queued -> running -> done|failed rather than a jump.
    queued.status = "running"
    db.save_run(queued, config_json=config_json)

    report = run_pipeline(config)
    report.run_id = queued.run_id  # keep the id the caller was given, not a fresh one
    db.save_run(report, config_json=config_json)
    return report


def list_runs(limit: int = 50) -> list[RunSummary]:
    """The most recent runs in run history, newest first, as summaries rather than full reports."""
    return [
        RunSummary(run_id=r.run_id, status=r.status, created_at=r.created_at) for r in db.list_run_history(limit=limit)
    ]


def get_run(run_id: str, *, details: bool = True) -> RunReport | None:
    """One run's report from run history, or None if there is no such run.

    With `details` false every result's `details` is emptied, keeping its status, summary and params.
    """
    record = db.get_run(run_id)
    if record is None:
        return None
    report = RunReport.model_validate_json(record.report_json)
    if not details:
        report.results = [result.model_copy(update={"details": {}}) for result in report.results]
    return report


def run_config(config_path: str | Path, *, save: bool = True) -> tuple[PipelineConfig, RunReport]:
    """Score a config file, saving the run to SQLite run history unless `save` is false."""
    config = load_config(config_path)
    report = run_pipeline(config)
    if save:
        db.save_run(report, config_json=config.model_dump_json())
    return config, report


def run_pipeline(config: PipelineConfig) -> RunReport:
    """Run every configured metric against the configured datasets.

    Every report is stamped with the metric definitions version it is scored
    with. A config pinned to another version fails the run before anything
    loads: its results would not be what the config was written to produce.
    A bad dataset (missing file, unreadable dump) fails the whole run, since
    nothing downstream can run without it. A bad *metric* (unknown id, a
    param that makes it raise) does not - it's caught and turned into its
    own `MetricResult(status="error")` so one broken metric can't discard
    every other metric's already-computed result.
    """
    report = RunReport(status="running", metric_definitions_version=METRIC_DEFINITIONS_VERSION)
    pinned = config.metric_definitions_version
    if pinned is not None and pinned != METRIC_DEFINITIONS_VERSION:
        current = METRIC_DEFINITIONS_VERSION
        return _failed(report, (
            f"config pins metric definitions v{pinned}, but this code scores with v{current}; score with a "
            f"config pinned to v{current} (configs/baseline_v{current}.yaml, or an enriched.yaml integrated "
            f"from it), or run this config from a commit that implements v{pinned}"
        ))
    try:
        datasets = load_datasets(config)
    except Exception as exc:
        return _failed(report, f"{type(exc).__name__}: {exc}")

    report.datasets = [DatasetSummary(name=name, counts=ds.counts()) for name, ds in datasets.items()]

    results: list[MetricResult] = []
    for index, metric_config in enumerate(config.metrics):
        params = dict(metric_config.params)
        try:
            metric = get_metric(metric_config.id)
            params = metric.resolve_params(metric_config.params)
            result = metric.run(datasets, metric_config.params)
        except Exception as exc:
            result = MetricResult(metric_id=metric_config.id, status="error", summary=f"{type(exc).__name__}: {exc}")
        results.append(result.model_copy(update={"index": index, "params": params}))
    report.results = results
    report.status = "done"
    report.finished_at = utcnow()
    return report


def _failed(report: RunReport, error: str) -> RunReport:
    report.status = "failed"
    report.error = error
    report.finished_at = utcnow()
    return report


def write_report(report: RunReport, path: str | Path) -> None:
    """Save a report as JSON, gzip-compressed when the file name ends in `.gz`
    (a saturation run lists every concept, so a full report runs to megabytes)."""
    path = Path(path)
    data = report.model_dump_json(indent=2).encode("utf-8")
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data)


def read_report(path: str | Path) -> RunReport:
    """Load a report saved by `write_report` (or `taxonomy-pipeline run --output`)."""
    path = Path(path)
    data = path.read_bytes()
    if path.suffix == ".gz":
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError, zlib.error) as exc:  # not gzip, truncated, or corrupted
            raise ValueError(f"{path} is not a readable gzip report: {exc}") from exc
    return RunReport.model_validate_json(data)


def load_report(source: str) -> RunReport:
    """A saved report file (`run --output`, `.json` or `.json.gz`), else a run ID from SQLite run history.

    Read-only: a mistyped path fails as a missing file, and run history is only queried when it exists, so a
    check never creates the database.
    """
    path = Path(source)
    if path.is_file():
        return read_report(path)
    if "/" in source or "\\" in source or path.suffix in {".json", ".gz"}:
        raise ValueError(f"report file {source} does not exist")
    if not db.DEFAULT_DB_PATH.is_file():
        raise ValueError(f"{source!r} is not a report file, and there is no run history at {db.DEFAULT_DB_PATH}")
    report = get_run(source)
    if report is None:
        raise ValueError(f"{source!r} is neither a report file nor a run ID in run history")
    return report


def check_runs(
    before: str, after: str, *, before_subject: str | None = None, after_subject: str | None = None,
) -> tuple[RunReport, RunReport, Comparability]:
    """Load two runs (files or run IDs) and check they were scored comparably."""
    before_report, after_report = load_report(before), load_report(after)
    check = check_comparable(before_report, after_report, before_subject=before_subject, after_subject=after_subject)
    return before_report, after_report, check


def result_labels(results: list[MetricResult]) -> list[str]:
    """A display label per result: the metric id, plus the params that tell repeated runs of it apart.

    `integrity` once stays `integrity`; run for two datasets it becomes
    `integrity (dataset=original)` and `integrity (dataset=skillsfuture)`. Only
    params whose values differ between the repeats are shown. Repeats those
    params still don't tell apart (identical or unrecorded params) get their
    config position too, `#<index>`, so every label in a report is unique.
    Mirrored by the dashboard's `metricLabels`; keep the two in step.
    """
    groups: dict[str, list[MetricResult]] = {}
    for result in results:
        groups.setdefault(result.metric_id, []).append(result)
    labels = []
    for result in results:
        group = groups[result.metric_id]
        params = [other.params or {} for other in group]
        keys = sorted({key for p in params for key in p})
        differing = [key for key in keys if len({_param_text(p.get(key)) for p in params}) > 1]
        if len(group) > 1 and differing and result.params is not None:
            shown = ", ".join(f"{key}={_param_text(result.params.get(key))}" for key in differing)
            labels.append(f"{result.metric_id} ({shown})")
        else:
            labels.append(result.metric_id)
    counts = Counter(labels)
    return [
        f"{label} #{result.index if result.index is not None else position}" if counts[label] > 1 else label
        for position, (label, result) in enumerate(zip(labels, results, strict=True))
    ]


def _param_text(value: Any) -> str:
    """One param value as label text, in the same format as the dashboard's `paramText`: JSON-like
    literals (true, false, null), whole floats as integers (85.0 -> 85, as JSON numbers arrive in the
    browser), top-level lists comma-joined, and objects with their keys sorted."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ",".join(_param_text(item) for item in value)
    if isinstance(value, dict):
        return "{" + ",".join(f"{key}:{_param_text(value[key])}" for key in sorted(value)) + "}"
    return str(value)
