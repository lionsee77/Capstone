"""Core orchestration: load the configured datasets, run the configured
metrics, build a report. Both the CLI (`cli.py`) and the FastAPI app
(`api/app.py`) call `run_pipeline` directly - neither talks to the other
over HTTP, so there is exactly one implementation of "what a run does".
"""
from __future__ import annotations

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
    for metric_config in config.metrics:
        try:
            metric = get_metric(metric_config.id)
            results.append(metric.run(datasets, metric_config.params))
        except Exception as exc:
            results.append(
                MetricResult(
                    metric_id=metric_config.id,
                    status="error",
                    summary=f"{type(exc).__name__}: {exc}",
                )
            )
    report.results = results
    report.status = "done"
    report.finished_at = utcnow()
    return report
