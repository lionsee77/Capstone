"""Core orchestration: load the configured datasets, run the configured
metrics, build a report. Both the CLI (`cli.py`) and the FastAPI app
(`api/app.py`) call `run_pipeline` directly - neither talks to the other
over HTTP, so there is exactly one implementation of "what a run does".
"""
from __future__ import annotations

from typing import Any

from .config import PipelineConfig
from .datasets import load_dataset
from .datasets.base import LoadedDataset
from .metrics.registry import get_metric
from .models import DatasetSummary, MetricResult, RunReport, utcnow


def load_datasets(config: PipelineConfig) -> dict[str, LoadedDataset]:
    return {ds_config.name: load_dataset(ds_config) for ds_config in config.datasets}


def run_pipeline(config: PipelineConfig) -> RunReport:
    """Run every configured metric against the configured datasets.

    A bad dataset (missing file, unreadable dump) fails the whole run, since
    nothing downstream can run without it. A bad *metric* (unknown id, a
    param that makes it raise) does not - it's caught and turned into its
    own `MetricResult(status="error")` so one broken metric can't discard
    every other metric's already-computed result.
    """
    report = RunReport(status="running")
    try:
        datasets = load_datasets(config)
    except Exception as exc:
        report.status = "failed"
        report.error = f"{type(exc).__name__}: {exc}"
        report.finished_at = utcnow()
        return report

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


def result_labels(results: list[MetricResult]) -> list[str]:
    """A display label per result: the metric id, plus the params that tell repeated runs of it apart.

    `integrity` once stays `integrity`; run for two datasets it becomes
    `integrity (dataset=original)` and `integrity (dataset=skillsfuture)`. Only
    params whose values differ between the repeats are shown. Repeats with
    identical (or unrecorded) params fall back to their config position, `#<index>`.
    """
    groups: dict[str, list[MetricResult]] = {}
    for result in results:
        groups.setdefault(result.metric_id, []).append(result)
    labels = []
    for position, result in enumerate(results):
        group = groups[result.metric_id]
        if len(group) == 1:
            labels.append(result.metric_id)
            continue
        params = [other.params or {} for other in group]
        differing = sorted(key for key in {k for p in params for k in p} if len({repr(p.get(key)) for p in params}) > 1)
        own = result.params or {}
        if differing and result.params is not None:
            shown = ", ".join(f"{key}={_param_text(own.get(key))}" for key in differing)
            labels.append(f"{result.metric_id} ({shown})")
        else:
            labels.append(f"{result.metric_id} #{result.index if result.index is not None else position}")
    return labels


def _param_text(value: Any) -> str:
    """Like the dashboard's `metricLabels` (JSON numbers): 85.0 prints as 85."""
    if isinstance(value, list):
        return ",".join(map(_param_text, value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
