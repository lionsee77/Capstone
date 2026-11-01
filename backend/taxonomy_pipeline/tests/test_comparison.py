"""Version-to-version comparison: changes and verdicts per measure, only for results scored alike."""
from __future__ import annotations

import pytest

from taxonomy_pipeline.models import MetricChange, RunComparison
from taxonomy_pipeline.quality.comparison import compare_reports
from tests.conftest import baseline_of_record
from tests.conftest import enriched_report as _enriched


def _metric(comparison: RunComparison, metric_id: str) -> MetricChange:
    return next(metric for metric in comparison.metrics if metric.metric_id == metric_id)


def _measure(comparison: RunComparison, metric_id: str, key: str):
    return next(measure for measure in _metric(comparison, metric_id).measures if measure.key == key)


def test_a_run_compared_with_itself_is_unchanged_on_every_measure() -> None:
    comparison = compare_reports(baseline_of_record(), baseline_of_record())

    assert comparison.comparability.comparable
    assert [metric.metric_id for metric in comparison.metrics] == ["integrity", "depth", "breadth", "saturation"]
    assert {measure.verdict for metric in comparison.metrics for measure in metric.measures} == {"unchanged"}


def test_each_measure_improves_or_deteriorates_in_its_own_better_direction() -> None:
    curve = baseline_of_record().results[3].details["curve"]
    after = _enriched(**{
        "breadth__occupational.matched_count": 24,
        "integrity__dead_ends.categories_with_zero_specialities": [1] * 10,
        "integrity__orphans.tags_unused_by_any_speciality": [1, 2, 3],
        "saturation__curve": [{**curve[0], "add_candidates": 300}],
    })

    comparison = compare_reports(baseline_of_record(), after)

    assert comparison.comparability.comparable
    breadth = _measure(comparison, "breadth", "occupational.matched_count")
    assert (breadth.before, breadth.after, breadth.change, breadth.verdict) == (16, 24, 8, "improved")
    assert breadth.relative_change == pytest.approx(0.5)
    assert _measure(comparison, "integrity", "dead_ends.categories_with_zero_specialities").verdict == "improved"
    unused = _measure(comparison, "integrity", "orphans.tags_unused_by_any_speciality")
    assert (unused.change, unused.verdict) == (2, "deteriorated")
    adds = _measure(comparison, "saturation", "add_candidates")
    assert (adds.before, adds.after, adds.verdict) == (665, 300, "improved")


def test_a_measure_with_no_better_direction_is_reported_as_changed() -> None:
    comparison = compare_reports(baseline_of_record(), _enriched(**{"depth__category_to_speciality.n": 120}))

    categories = _measure(comparison, "depth", "category_to_speciality.n")
    assert (categories.better, categories.change, categories.verdict) == (None, 10, "changed")


def test_differences_below_rounding_noise_count_as_unchanged() -> None:
    comparison = compare_reports(baseline_of_record(), _enriched(**{"depth__speciality_to_tag.mean": 2.43 + 1e-12}))

    mean = _measure(comparison, "depth", "speciality_to_tag.mean")
    assert (mean.change, mean.verdict) == (0, "unchanged")


def test_relative_change_is_left_out_when_the_before_value_is_zero() -> None:
    after = _enriched(**{"integrity__orphans.dangling_speciality_tag_links": 4})

    comparison = compare_reports(baseline_of_record(), after)

    dangling = _measure(comparison, "integrity", "orphans.dangling_speciality_tag_links")
    assert (dangling.change, dangling.relative_change, dangling.verdict) == (4, None, "deteriorated")


def test_runs_scored_with_different_definitions_show_values_but_no_changes_or_verdicts() -> None:
    after = _enriched(**{"breadth__occupational.matched_count": 24})
    after.metric_definitions_version = None

    comparison = compare_reports(baseline_of_record(), after)

    assert not comparison.comparability.comparable
    breadth = _measure(comparison, "breadth", "occupational.matched_count")
    assert (breadth.before, breadth.after, breadth.change, breadth.verdict) == (16, 24, None, "not_compared")
    assert {m.verdict for metric in comparison.metrics for m in metric.measures} == {"not_compared"}


def test_only_the_pair_scored_differently_goes_uncompared() -> None:
    after = _enriched()
    breadth = next(result for result in after.results if result.metric_id == "breadth")
    breadth.params["semantic_threshold"] = 0.5

    comparison = compare_reports(baseline_of_record(), after)

    assert [issue.kind for issue in _metric(comparison, "breadth").issues] == ["settings"]
    assert {m.verdict for m in _metric(comparison, "breadth").measures} == {"not_compared"}
    assert {m.verdict for m in _metric(comparison, "depth").measures} == {"unchanged"}


def test_a_result_only_one_run_has_is_listed_with_its_own_values() -> None:
    after = _enriched()
    after.results = [result for result in after.results if result.metric_id != "saturation"]

    comparison = compare_reports(baseline_of_record(), after)

    saturation = _metric(comparison, "saturation")
    assert (saturation.before_index, saturation.after_index) == (3, None)
    adds = next(m for m in saturation.measures if m.key == "add_candidates")
    assert (adds.before, adds.after, adds.verdict) == (665, None, "not_compared")


def test_a_result_that_did_not_run_has_no_measures() -> None:
    after = _enriched()
    after.results[1].status = "error"

    comparison = compare_reports(baseline_of_record(), after)

    depth = _metric(comparison, "depth")
    assert [issue.kind for issue in depth.issues] == ["result_status"]
    assert {m.after for m in depth.measures} == {None}


def test_labels_name_each_result_for_display() -> None:
    labels = ["integrity (dataset=original)", "depth", "breadth", "saturation"]
    comparison = compare_reports(baseline_of_record(), _enriched(), before_labels=labels)

    assert comparison.metrics[0].label == "integrity (dataset=original)"
