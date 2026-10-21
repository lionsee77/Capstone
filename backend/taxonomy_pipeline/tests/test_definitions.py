"""Metric definitions: the version pins how runs are scored, and two runs only compare when scored alike."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from taxonomy_pipeline.config import load_config
from taxonomy_pipeline.definitions import (
    METRIC_DEFINITIONS_VERSION,
    check_comparable,
    definitions_snapshot,
    report_subject,
)
from taxonomy_pipeline.metrics.registry import METRICS
from taxonomy_pipeline.models import Comparability, DatasetSummary, MetricResult, RunReport
from taxonomy_pipeline.pipeline import read_report

PROJECT = Path(__file__).resolve().parents[1]

# What each metric definitions version pins in code: every scoring metric's defaults, plus the fixed parts of
# its formula no param exposes. If the test below fails, one of them changed, and with it what every run
# measures: bump METRIC_DEFINITIONS_VERSION, pin the new snapshot here under the new number (keep the old
# ones), add configs/baseline_v<N>.yaml and re-score the baseline of record (README, "Official metric
# definitions"). Don't edit an existing version's entry to make the test pass.
PINNED_DEFINITIONS: dict[int, dict[str, Any]] = {
    1: {
        "integrity": {"default_params": {"dataset": None, "fuzzy_threshold": 85.0}},
        "depth": {"default_params": {"dataset": None}},
        "breadth": {
            "default_params": {
                "target": None, "ssic_reference": "ssic", "ssoc_reference": "ssoc",
                "industry_level": "division", "occupational_level": "minor_group", "industry_service_id": 1,
                "threshold": 75.0, "preprocessing": "typography_v1",
                "matching_method": "semantic", "semantic_threshold": 0.6, "top_k": 5,
            },
            "embedding": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
                "text_profile": "semantic_labels_v1",
            },
        },
        "saturation": {
            "default_params": {
                "baseline": None, "incoming": None, "threshold": 90.0,
                "semantic_model": None, "semantic_model_revision": None, "semantic_threshold": 0.7,
                "add_candidate_lexical_threshold": 60.0, "add_candidate_semantic_threshold": 0.5,
                "cross_level": True, "semantic_threshold_by_level": {}, "judge": None,
            },
            "judge_defaults": {
                "model": "gpt-6-luna", "candidates": 5, "same_at_or_above": 0.8, "new_at_or_above": 0.8,
                "levels": ["tags"], "cache": None, "workers": 8,
            },
        },
    },
}


def test_scoring_defaults_and_pinned_constants_match_the_current_definitions_version() -> None:
    assert definitions_snapshot() == PINNED_DEFINITIONS[METRIC_DEFINITIONS_VERSION], (
        "A scoring metric's defaults or pinned constants changed without a new metric definitions version: "
        "bump METRIC_DEFINITIONS_VERSION, pin the new snapshot here and re-score the baseline (README)."
    )


def _baseline_config_path(version: int = METRIC_DEFINITIONS_VERSION) -> Path:
    return PROJECT / "configs" / f"baseline_v{version}.yaml"


def test_the_baseline_config_pins_the_current_version_and_scores_the_original_with_every_scoring_metric() -> None:
    config = load_config(_baseline_config_path())

    assert config.metric_definitions_version == METRIC_DEFINITIONS_VERSION
    scoring = sorted(metric_id for metric_id, metric in METRICS.items() if metric.subject_param)
    assert sorted(entry.id for entry in config.metrics) == scoring
    assert {entry.params[METRICS[entry.id].subject_param] for entry in config.metrics} == {"original"}


def test_the_baseline_config_depends_on_nothing_that_can_drift() -> None:
    params = {entry.id: entry.params for entry in load_config(_baseline_config_path()).metrics}

    # Breadth's sentence model is pinned in code; saturation's must be pinned here. An LLM judge in public
    # beta can't reproduce its verdicts, so it stays off for scoring.
    assert params["saturation"].get("semantic_model_revision") or not params["saturation"].get("semantic_model")
    assert params["saturation"].get("judge") is None


def test_the_stored_baseline_of_record_came_from_the_pinned_config_and_is_named_in_the_readme() -> None:
    report = read_report(PROJECT / "baselines" / f"baseline_v{METRIC_DEFINITIONS_VERSION}.json.gz")
    config = load_config(_baseline_config_path())

    assert report.status == "done" and report.metric_definitions_version == METRIC_DEFINITIONS_VERSION
    assert all(result.status == "ok" for result in report.results)
    # Every result was produced by the config's entry, over the current defaults.
    assert [(result.metric_id, result.params) for result in report.results] == [
        (entry.id, METRICS[entry.id].resolve_params(entry.params)) for entry in config.metrics
    ]
    assert report.run_id in (PROJECT / "README.md").read_text(encoding="utf-8")


def _result(metric_id: str, index: int, *, status: str = "ok", **params: Any) -> MetricResult:
    return MetricResult(metric_id=metric_id, status=status, summary=f"{metric_id} result", index=index, params=params)


def _report(
    subject: str = "original", *, version: int | None = METRIC_DEFINITIONS_VERSION,
    results: list[MetricResult] | None = None, ssic_divisions: int = 82, **fields: Any,
) -> RunReport:
    """A run scoring `subject` with integrity and semantic breadth, as the baseline config does."""
    if results is None:
        results = [
            _result("integrity", 0, dataset=subject, fuzzy_threshold=85.0),
            _result("breadth", 1, target=subject, ssic_reference="ssic", ssoc_reference="ssoc",
                    matching_method="semantic", semantic_threshold=0.6),
        ]
    datasets = [
        DatasetSummary(name=subject, counts={"categories": 110, "tags": 1000}),
        DatasetSummary(name="ssic", counts={"division": ssic_divisions}),
        DatasetSummary(name="ssoc", counts={"minor_group": 145}),
    ]
    return RunReport(status="done", metric_definitions_version=version, datasets=datasets, results=results, **fields)


def _pairs(check: Comparability) -> list[tuple[str, int | None, int | None]]:
    return [(metric.metric_id, metric.before_index, metric.after_index) for metric in check.metrics]


def test_runs_scored_alike_compare_even_though_they_score_different_taxonomies() -> None:
    check = check_comparable(_report("original"), _report("enriched"))

    assert check.comparable and check.model_dump()["comparable"] is True
    assert (check.before_subject, check.after_subject) == ("original", "enriched")
    assert _pairs(check) == [("integrity", 0, 0), ("breadth", 1, 1)]
    assert check.issues == [] and all(metric.issues == [] for metric in check.metrics)


def test_runs_scored_with_different_definitions_versions_are_not_comparable() -> None:
    check = check_comparable(_report("original"), _report("enriched", version=METRIC_DEFINITIONS_VERSION + 1))

    assert not check.comparable
    assert [issue.kind for issue in check.issues] == ["definitions_version"]
    assert f"v{METRIC_DEFINITIONS_VERSION} and the after run with v{METRIC_DEFINITIONS_VERSION + 1}" in (
        check.issues[0].message
    )
    # The same settings still pair, but every pair is covered by the run-wide issue.
    assert _pairs(check) == [("integrity", 0, 0), ("breadth", 1, 1)]


def test_a_report_saved_before_runs_were_stamped_has_no_version_and_is_flagged() -> None:
    legacy = RunReport.model_validate({"run_id": "old", "status": "done", "results": [
        {"metric_id": "integrity", "summary": "s", "index": 0, "params": {"dataset": "original"}},
    ]})
    assert legacy.metric_definitions_version is None  # unknown, never assumed current

    check = check_comparable(legacy, _report("enriched"))

    assert not check.comparable
    assert "before run records no metric definitions version" in check.issues[0].message


def test_results_saved_without_params_are_flagged_as_unknown_settings() -> None:
    legacy = _report(version=None, results=[MetricResult(metric_id="integrity", summary="s")])

    with pytest.raises(ValueError, match="records no scored taxonomy"):
        report_subject(legacy)
    check = check_comparable(legacy, _report("enriched"), before_subject="original")

    assert [issue.kind for issue in check.issues] == ["definitions_version", "settings"]
    assert "1 scoring result(s) in the before run record no params" in check.issues[1].message


def test_a_changed_setting_is_flagged_with_what_changed() -> None:
    after = _report("enriched")
    after.results[1].params.update(matching_method="lexical", threshold=75.0)

    check = check_comparable(_report("original"), after)

    assert not check.comparable
    assert _pairs(check) == [("integrity", 0, 0), ("breadth", 1, 1)]
    [issue] = check.metrics[1].issues
    assert issue.kind == "settings"
    assert issue.message == (
        'scored with different settings: matching_method "semantic" -> "lexical"; threshold unset -> 75.0'
    )
    assert check.metrics[0].issues == []  # integrity still compares


def test_a_metric_only_one_run_scored_is_flagged_as_missing() -> None:
    after = _report("enriched")
    after.results = after.results[:1]

    check = check_comparable(_report("original"), after)

    assert _pairs(check) == [("integrity", 0, 0), ("breadth", 1, None)]
    assert check.metrics[1].issues[0].kind == "missing_result"
    assert check.metrics[1].issues[0].message == "the after run has no breadth result for enriched"


def test_a_result_that_did_not_score_is_flagged() -> None:
    after = _report("enriched")
    after.results[0] = after.results[0].model_copy(update={"status": "error", "summary": "dataset not loaded"})

    check = check_comparable(_report("original"), after)

    assert [issue.kind for issue in check.metrics[0].issues] == ["result_status"]
    assert check.metrics[0].issues[0].message == "the after result is error: dataset not loaded"


def test_a_reference_dataset_that_changed_between_runs_is_flagged_for_the_results_that_read_it() -> None:
    check = check_comparable(_report("original"), _report("enriched", ssic_divisions=81))

    assert check.metrics[0].issues == []  # integrity doesn't read ssic
    [issue] = check.metrics[1].issues
    assert issue.kind == "reference_data"
    assert issue.message == (
        "ssic, which this result reads, has different row counts in the two runs (division 82 -> 81)"
    )


def test_repeated_results_pair_by_their_settings_not_their_position() -> None:
    def breadth(subject: str, index: int, method: str) -> MetricResult:
        return _result("breadth", index, target=subject, matching_method=method)

    before = _report(results=[breadth("original", 0, "semantic"), breadth("original", 1, "lexical")])
    after = _report("enriched", results=[breadth("enriched", 0, "lexical"), breadth("enriched", 1, "semantic")])

    check = check_comparable(before, after)

    assert check.comparable
    assert _pairs(check) == [("breadth", 1, 0), ("breadth", 0, 1)]


def test_the_subject_must_be_named_when_a_run_scores_several_taxonomies() -> None:
    before = _report(results=[
        _result("integrity", 0, dataset="original", fuzzy_threshold=85.0),
        _result("integrity", 1, dataset="skillsfuture", fuzzy_threshold=85.0),
    ])

    with pytest.raises(ValueError, match=r"several taxonomies \(original, skillsfuture\)"):
        check_comparable(before, _report("enriched"))
    check = check_comparable(before, _report("enriched"), before_subject="original")

    # Only the named taxonomy's results take part; breadth ran on the after side alone.
    assert _pairs(check) == [("integrity", 0, 0), ("breadth", None, 1)]
    assert check.metrics[0].issues == []


def test_a_run_that_did_not_finish_is_flagged() -> None:
    after = _report("enriched", error="FileNotFoundError: additions.sql")
    after.status = "failed"

    check = check_comparable(_report("original"), after)

    assert check.issues[0].kind == "run_status"
    assert check.issues[0].message == "the after run is failed: FileNotFoundError: additions.sql"
