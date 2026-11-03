from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from taxonomy_pipeline.app.scoring import (
    check_runs,
    execute_queued_run,
    get_run,
    list_runs,
    load_report,
    load_taxonomies,
    metric_catalogue,
    queue_run,
    read_report,
    result_labels,
    run_config,
    run_pipeline,
    write_report,
)
from taxonomy_pipeline.config import DatasetConfig, MetricConfig, PipelineConfig
from taxonomy_pipeline.models import MetricResult, RunReport
from taxonomy_pipeline.quality.definitions import METRIC_DEFINITIONS_VERSION
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


def test_every_run_records_the_metric_definitions_it_was_scored_with(classification_paths):
    assert run_pipeline(_config(classification_paths)).metric_definitions_version == METRIC_DEFINITIONS_VERSION


def test_a_config_pinned_to_other_metric_definitions_fails_before_scoring_anything(classification_paths):
    config = _config(classification_paths)
    config.metric_definitions_version = METRIC_DEFINITIONS_VERSION + 1
    config.datasets[0].paths = ["/no/such/file.sql"]  # never read: the pin is checked first

    report = run_pipeline(config)

    assert report.status == "failed" and report.results == []
    assert f"config pins metric definitions v{METRIC_DEFINITIONS_VERSION + 1}" in report.error


def test_a_config_pinned_to_the_current_metric_definitions_runs(classification_paths):
    config = _config(classification_paths)
    config.metric_definitions_version = METRIC_DEFINITIONS_VERSION

    assert run_pipeline(config).status == "done"


@pytest.mark.parametrize("name", ["report.json", "report.json.gz"])
def test_a_saved_report_reads_back_unchanged_compressed_or_not(tmp_path: Path, name: str) -> None:
    report = RunReport(status="done", metric_definitions_version=METRIC_DEFINITIONS_VERSION, results=[
        MetricResult(metric_id="depth", summary="s", index=0, params={"dataset": "original"}),
    ])

    write_report(report, tmp_path / name)

    assert read_report(tmp_path / name) == report
    assert (tmp_path / name).read_bytes().startswith(b"\x1f\x8b") == name.endswith(".gz")  # gzip magic number


def test_a_damaged_gzip_report_is_a_clear_error_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "report.json.gz"
    write_report(RunReport(status="done"), path)
    path.write_bytes(path.read_bytes()[:-8])  # cut off, as by an interrupted copy

    with pytest.raises(ValueError, match="not a readable gzip report"):
        read_report(path)


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
    report = RunReport.model_validate(old)

    assert report.results[0].index is None and report.results[0].params is None
    assert report.metric_definitions_version is None  # unknown, not assumed current


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


def _config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "datasets": [
            {"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]},
            {"name": "ssic", "type": "ssic", "paths": [str(tmp_path / "missing.xlsx")]},
        ],
        "metrics": [{"id": "integrity", "params": {"dataset": "original"}}],
    }), encoding="utf-8")
    return path


def test_load_taxonomies_loads_only_the_named_datasets(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    assert set(load_taxonomies(config, "original")) == {"original"}  # the missing workbook is never opened
    with pytest.raises(ValueError, match="declares no taxonomy dataset named 'nope'"):
        load_taxonomies(config, "original", "nope")


def test_run_config_saves_to_run_history_unless_told_not_to(tmp_path: Path, isolated_db: Path) -> None:
    from taxonomy_pipeline import db

    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "datasets": [{"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]}],
        "metrics": [{"id": "integrity", "params": {"dataset": "original"}}],
    }), encoding="utf-8")
    _, unsaved = run_config(config, save=False)
    assert unsaved.status == "done" and db.get_run(unsaved.run_id) is None
    _, saved = run_config(config)
    assert db.get_run(saved.run_id) is not None


def test_load_report_reads_a_file_or_a_run_id_from_history(tmp_path: Path, isolated_db: Path) -> None:
    from taxonomy_pipeline import db

    report = RunReport(status="done")
    path = tmp_path / "report.json"
    write_report(report, path)
    assert load_report(str(path)) == report
    with pytest.raises(ValueError, match="there is no run history"):
        load_report(report.run_id)
    db.save_run(report, config_json="{}")
    assert load_report(report.run_id).run_id == report.run_id
    with pytest.raises(ValueError, match="does not exist"):
        load_report(str(tmp_path / "missing.json"))


def test_check_runs_loads_both_runs_and_compares_them(tmp_path: Path) -> None:
    report = RunReport(status="done", metric_definitions_version=METRIC_DEFINITIONS_VERSION)
    path = tmp_path / "report.json"
    write_report(report, path)
    before, after, check = check_runs(str(path), str(path), before_subject="original", after_subject="original")
    assert before == after == report
    assert check.before_run_id == check.after_run_id == report.run_id


def test_the_metric_catalogue_describes_every_scoring_metric() -> None:
    catalogue = metric_catalogue()
    assert set(catalogue) == {"integrity", "depth", "breadth", "saturation"}
    assert catalogue["integrity"].description and isinstance(catalogue["integrity"].default_params, dict)


def test_a_queued_run_is_polled_through_history_until_it_finishes(isolated_db: Path) -> None:
    config = PipelineConfig(
        datasets=[DatasetConfig(name="original", paths=[str(FIXTURES / "mini_original.sql")])],
        metrics=[MetricConfig(id="integrity", params={"dataset": "original"})],
    )
    queued = queue_run(config)
    assert get_run(queued.run_id).status == "queued"
    assert [(row.run_id, row.status) for row in list_runs()] == [(queued.run_id, "queued")]

    finished = execute_queued_run(queued, config)

    assert finished.run_id == queued.run_id and get_run(queued.run_id).status == "done"
    assert get_run("missing") is None


def test_a_stored_run_can_be_read_without_result_details(isolated_db) -> None:
    from taxonomy_pipeline import db

    result = MetricResult(metric_id="saturation", summary="s", details={"concepts": list(range(5))}, params={"a": 1})
    db.save_run(RunReport(run_id="r1", status="done", results=[result]), config_json="{}")

    light = get_run("r1", details=False)

    assert light.results[0].details == {} and light.results[0].summary == "s" and light.results[0].params == {"a": 1}
    assert get_run("r1").results[0].details == {"concepts": [0, 1, 2, 3, 4]}
