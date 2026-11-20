"""Version-to-version comparison: each metric's headline numbers in two runs, the
change, and whether it got better (PB10).

It builds on `definitions.check_comparable`, which pairs each scoring result
of one run with the result of the other scored the same way and lists what
stops a pair comparing. A change is only worth reading for a pair scored
alike, so a measure gets a `change` and a verdict only when neither the runs
nor its pair have an issue; otherwise its values are shown for reference with
the verdict "not_compared".

Which numbers are compared, and which way is better, belongs to each metric
(`Metric.measures`), so a new scoring metric joins comparisons by overriding
that one method. The verdict here is strict - any change in a direction counts.
Tolerances for "unchanged" (PB11) are applied on top of this report, not
inside it, so the raw change is always visible.
"""
from __future__ import annotations

import math

from ..models import (
    ComparabilityIssue,
    Measure,
    MeasureChange,
    MetricChange,
    MetricResult,
    RunComparison,
    RunReport,
    Verdict,
)
from .definitions import check_comparable
from .registry import METRICS

# Absolute difference below which two values are equal: above the 1e-7 that
# sentence-model scores can differ by between machines, and well under the
# rounding every metric applies (3 decimals).
_EQUAL = 1e-6


def compare_reports(
    before: RunReport, after: RunReport, *,
    before_labels: list[str] | None = None, after_labels: list[str] | None = None,
    before_subject: str | None = None, after_subject: str | None = None,
) -> RunComparison:
    """Compare `after` (e.g. an enriched snapshot's run) with `before` (e.g. the baseline of record).

    Labels name each result for display, one per entry in the report's `results`; they default to
    the metric id. Subjects are as in `check_comparable`, which raises the same errors.
    """
    check = check_comparable(before, after, before_subject=before_subject, after_subject=after_subject)
    labels = {
        "before": before_labels or [result.metric_id for result in before.results],
        "after": after_labels or [result.metric_id for result in after.results],
    }
    metrics = []
    # Pairs take the before run's label; a result only the after run has could share one
    # (e.g. both runs have "integrity #0", scored differently), so it is marked as the after run's.
    used = {labels["before"][pair.before_index] for pair in check.metrics if pair.before_index is not None}
    for pair in check.metrics:
        old = before.results[pair.before_index] if pair.before_index is not None else None
        new = after.results[pair.after_index] if pair.after_index is not None else None
        if pair.before_index is not None:
            label = labels["before"][pair.before_index]
        else:
            label = labels["after"][pair.after_index]
            label = f"{label} (after)" if label in used else label
        issues = list(pair.issues)
        old_measures = _measures(pair.metric_id, old, "before", issues)
        new_measures = _measures(pair.metric_id, new, "after", issues)
        compared = not check.issues and not issues
        metrics.append(MetricChange(
            metric_id=pair.metric_id, label=label, before_index=pair.before_index, after_index=pair.after_index,
            issues=issues, measures=_measure_changes(old_measures, new_measures, compared=compared),
        ))
    return RunComparison(comparability=check, metrics=metrics)


def _measures(
    metric_id: str, result: MetricResult | None, side: str, issues: list[ComparabilityIssue],
) -> dict[str, Measure]:
    """The result's measures by key; none for a missing result or one that didn't run.

    A stored report whose details don't have the shape `measures()` reads (hand-edited, or
    from an older version) adds an issue instead of failing the whole comparison, so its pair
    goes uncompared and the rest of the report is still served."""
    metric = METRICS.get(metric_id)
    if result is None or metric is None or result.status != "ok":
        return {}
    try:
        return {measure.key: measure for measure in metric.measures(result)}
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        issues.append(ComparabilityIssue(
            kind="result_status",
            message=f"the {side} run's {metric_id} details couldn't be read ({type(exc).__name__}: {exc})",
        ))
        return {}


def _measure_changes(old: dict[str, Measure], new: dict[str, Measure], *, compared: bool) -> list[MeasureChange]:
    changes = []
    for key in dict.fromkeys([*old, *new]):
        shape = old.get(key) or new[key]
        values = (old[key].value if key in old else None, new[key].value if key in new else None)
        changes.append(_change(shape, *values, compared=compared))
    return changes


def _change(shape: Measure, before: float | None, after: float | None, *, compared: bool) -> MeasureChange:
    common = {"key": shape.key, "label": shape.label, "better": shape.better, "unit": shape.unit,
              "before": before, "after": after}
    if not compared or before is None or after is None:
        return MeasureChange(**common, verdict="not_compared")
    change = 0.0 if math.isclose(before, after, rel_tol=0, abs_tol=_EQUAL) else after - before
    return MeasureChange(
        **common, change=change, relative_change=change / abs(before) if before else None,
        verdict=_verdict(shape, change),
    )


def _verdict(shape: Measure, change: float) -> Verdict:
    if change == 0:
        return "unchanged"
    if shape.better is None:
        return "changed"
    return "improved" if (change > 0) == (shape.better == "higher") else "deteriorated"
