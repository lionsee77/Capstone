from __future__ import annotations

from pathlib import Path
from typing import Any

from taxonomy_pipeline.config import DatasetConfig, MetricConfig, PipelineConfig
from taxonomy_pipeline.models import MetricResult, RunReport
from taxonomy_pipeline.pipeline import result_labels, run_pipeline
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
                id="breadth", params={"target": "original", "matching_method": "lexical", "threshold": 50}
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


def test_each_result_records_its_config_entry_and_resolved_params(classification_paths: dict[str, Path]) -> None:
    config = _config(classification_paths)
    config.metrics = [
        MetricConfig(id="integrity", params={"dataset": "original"}),
        MetricConfig(id="integrity", params={"dataset": "skillsfuture", "fuzzy_threshold": 70}),
        MetricConfig(id="no_such_metric", params={"x": 1}),
    ]
    report = run_pipeline(config)

    assert [result.index for result in report.results] == [0, 1, 2]
    assert report.results[0].params == {"dataset": "original", "fuzzy_threshold": 85.0}  # default filled in
    assert report.results[1].params == {"dataset": "skillsfuture", "fuzzy_threshold": 70}
    assert report.results[2].status == "error" and report.results[2].params == {"x": 1}  # as given
    assert result_labels(report.results) == [
        "integrity (dataset=original, fuzzy_threshold=85)",
        "integrity (dataset=skillsfuture, fuzzy_threshold=70)",
        "no_such_metric",
    ]


def test_reports_saved_before_results_had_an_index_still_load() -> None:
    old = {"run_id": "r", "status": "done", "results": [{"metric_id": "depth", "summary": "s"}]}
    result = RunReport.model_validate(old).results[0]

    assert result.index is None and result.params is None


def _result(index: int, params: dict[str, Any], metric_id: str = "depth") -> MetricResult:
    return MetricResult(metric_id=metric_id, summary="", index=index, params=params)


def test_labels_fall_back_to_position_when_params_dont_tell_repeats_apart() -> None:
    result = _result

    assert result_labels([result(0, {"dataset": "a"}), result(3, {"dataset": "a"})]) == ["depth #0", "depth #3"]
    legacy = [MetricResult(metric_id="depth", summary=""), MetricResult(metric_id="depth", summary="")]
    assert result_labels(legacy) == ["depth #0", "depth #1"]
    assert result_labels([result(0, {"incoming": ["a", "b"]}), result(1, {"incoming": ["c"]})]) == [
        "depth (incoming=a,b)", "depth (incoming=c)",
    ]


def test_repeats_sharing_their_differing_params_also_get_their_position() -> None:
    results = [_result(0, {"dataset": "a"}, "integrity"), _result(1, {"dataset": "a"}, "integrity"),
               _result(2, {"dataset": "b"}, "integrity")]

    assert result_labels(results) == ["integrity (dataset=a) #0", "integrity (dataset=a) #1", "integrity (dataset=b)"]


def test_label_values_keep_objects_booleans_and_nulls_readable() -> None:
    results = [
        _result(0, {"by_level": {"tags": 0.8, "specialities": 0.75}, "cross_level": True, "model": None}),
        _result(1, {"by_level": {"tags": 0.9}, "cross_level": False, "model": "m"}),
    ]

    assert result_labels(results) == [
        "depth (by_level={specialities:0.75,tags:0.8}, cross_level=true, model=null)",
        "depth (by_level={tags:0.9}, cross_level=false, model=m)",
    ]
