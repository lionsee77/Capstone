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


def test_sentence_model_noise_between_machines_counts_as_unchanged_but_a_rounded_step_does_not() -> None:
    noisy = compare_reports(baseline_of_record(), _enriched(**{"depth__speciality_to_tag.mean": 2.43 + 3e-7}))
    stepped = compare_reports(baseline_of_record(), _enriched(**{"depth__speciality_to_tag.mean": 2.431}))

    noise = _measure(noisy, "depth", "speciality_to_tag.mean")
    assert (noise.change, noise.verdict) == (0, "unchanged")
    assert _measure(stepped, "depth", "speciality_to_tag.mean").verdict == "improved"


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


def test_unreadable_stored_details_leave_their_pair_uncompared_instead_of_failing() -> None:
    after = _enriched()
    integrity = next(result for result in after.results if result.metric_id == "integrity")
    integrity.details["exact_duplicates"]["categories"] = [{"name": "accounting"}]  # a cluster without ids

    comparison = compare_reports(baseline_of_record(), after)

    metric = _metric(comparison, "integrity")
    assert [issue.kind for issue in metric.issues] == ["result_status"]
    assert "after run's integrity details couldn't be read (KeyError" in metric.issues[0].message
    assert {m.verdict for m in metric.measures} == {"not_compared"}
    assert {m.verdict for m in _metric(comparison, "depth").measures} == {"unchanged"}
    assert comparison.comparability.comparable and not comparison.comparable


def test_a_result_only_the_after_run_has_never_reuses_a_before_label() -> None:
    def two_integrity_results(report, thresholds):
        integrity = report.results[0]
        report.results = [integrity.model_copy(deep=True, update={"params": {**integrity.params,
                                                                              "fuzzy_threshold": threshold}})
                          for threshold in thresholds]
        return report

    labels = ["integrity #0", "integrity #1"]
    comparison = compare_reports(two_integrity_results(baseline_of_record(), [85, 90]),
                                 two_integrity_results(_enriched(), [80, 95]),
                                 before_labels=labels, after_labels=labels)

    assert [metric.label for metric in comparison.metrics] == [
        "integrity #0", "integrity #1", "integrity #0 (after)", "integrity #1 (after)",
    ]


def test_rates_are_marked_as_rates_and_counts_as_numbers() -> None:
    comparison = compare_reports(baseline_of_record(), _enriched())

    rates = {m.key for metric in comparison.metrics for m in metric.measures if m.unit == "rate"}
    assert rates == {"dead_ends.category_dead_end_rate", "dead_ends.speciality_dead_end_rate",
                     "industry.coverage", "occupational.coverage", "new_rate_min", "new_rate_max"}
