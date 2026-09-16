from __future__ import annotations

from taxonomy_pipeline.config import DatasetConfig, MetricConfig, PipelineConfig
from taxonomy_pipeline.pipeline import run_pipeline
from tests.conftest import FIXTURES


def _config(classification_paths) -> PipelineConfig:
    return PipelineConfig(
        datasets=[
            DatasetConfig(name="original", paths=[str(FIXTURES / "mini_original.sql")]),
            DatasetConfig(
                name="skillsfuture",
                paths=[str(FIXTURES / "mini_reference.sql")],
                table_map={"categories": "sf_categories"},
            ),
            *[DatasetConfig(name=kind, type=kind, paths=[str(path)])
              for kind, path in classification_paths.items()],
        ],
        metrics=[
            MetricConfig(id="integrity", params={"dataset": "original"}),
            MetricConfig(id="depth", params={"dataset": "original"}),
            MetricConfig(
                id="breadth", params={"target": "original", "threshold": 50}
            ),
            MetricConfig(id="saturation", params={"baseline": "original", "incoming": "skillsfuture"}),
        ],
    )


def test_full_run_against_fixture_datasets(classification_paths):
    report = run_pipeline(_config(classification_paths))

    assert report.status == "done"
    assert report.error is None
    assert {d.name for d in report.datasets} == {"original", "skillsfuture", "ssic", "ssoc"}

    by_metric = {r.metric_id: r for r in report.results}
    assert by_metric["integrity"].status == "ok"
    assert by_metric["depth"].status == "ok"
    assert by_metric["breadth"].status == "ok"
    assert by_metric["saturation"].status == "ok"


def test_unknown_metric_id_fails_only_that_metric_not_the_whole_run(classification_paths):
    config = _config(classification_paths)
    config.metrics.append(MetricConfig(id="not_a_real_metric"))
    report = run_pipeline(config)

    # the other, valid metrics still produced results...
    assert report.status == "done"
    by_metric = {r.metric_id: r for r in report.results}
    assert by_metric["integrity"].status == "ok"
    # ...and the bad one surfaces as its own error, not a run-wide failure.
    assert by_metric["not_a_real_metric"].status == "error"
    assert "not_a_real_metric" in by_metric["not_a_real_metric"].summary


def test_unloadable_dataset_fails_the_whole_run(classification_paths):
    config = _config(classification_paths)
    config.datasets[0].paths = ["/no/such/file.sql"]
    report = run_pipeline(config)
    assert report.status == "failed"
    assert report.error is not None
