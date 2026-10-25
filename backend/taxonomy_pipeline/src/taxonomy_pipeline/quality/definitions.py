"""Metric definitions: which formulas and defaults a run is scored with, and
whether two runs were scored alike.

A run's numbers depend on how each metric scores as much as on the taxonomy:
switching breadth to semantic matching (#79) moved the original taxonomy's
industry coverage from 0% to 22% with the taxonomy unchanged. A before/after
comparison (PB11) therefore only means something between runs scored the same
way, and this module makes that checkable:

- `METRIC_DEFINITIONS_VERSION` names the formulas and defaults of every scoring
  metric (one with a `subject_param`), and `app.scoring.run_pipeline` stamps it on
  every report. Bump it whenever one of them changes - a formula, a
  normalisation step, a default, a pinned model - then pin the new
  `definitions_snapshot()` in `tests/test_definitions.py`, add
  `configs/baseline_v<N>.yaml` and re-score the baseline of record (README,
  "Official metric definitions"). That test catches a changed default or
  pinned constant; a formula change has no such tripwire, so the bump is up to
  its author and reviewer.
- `check_comparable` takes two reports and lists everything that stops their
  numbers comparing: a failed run, definitions versions that differ or were
  never recorded, and for each scoring result a missing counterpart, an error,
  different settings (every param except the subject, which is the one thing
  a baseline run and an enriched run should differ in), or a reference or
  incoming dataset whose row counts changed in between.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..models import Comparability, ComparabilityIssue, MetricComparison, MetricResult, RunReport
from .registry import METRICS

METRIC_DEFINITIONS_VERSION = 1


def definitions_snapshot() -> dict[str, dict[str, Any]]:
    """What the current definitions pin in code: each scoring metric's `definition()`, by metric id."""
    return {metric_id: metric.definition() for metric_id, metric in METRICS.items() if metric.subject_param}


@dataclass(frozen=True)
class _Scored:
    """A result scoring the compared taxonomy, and the params its counterpart must share (all but the subject)."""

    index: int
    result: MetricResult
    settings: dict[str, Any]


def _subject_param(metric_id: str) -> str | None:
    metric = METRICS.get(metric_id)
    return metric.subject_param if metric else None


def report_subject(report: RunReport) -> str:
    """The one taxonomy a run scores, read from its scoring results' subject params."""
    subjects = []
    for result in report.results:
        param = _subject_param(result.metric_id)
        subject = (result.params or {}).get(param) if param else None
        if isinstance(subject, str):
            subjects.append(subject)
    unique = list(dict.fromkeys(subjects))
    if len(unique) == 1:
        return unique[0]
    if unique:
        raise ValueError(f"run {report.run_id} scores several taxonomies ({', '.join(unique)}); name the one to use")
    raise ValueError(f"run {report.run_id} records no scored taxonomy: no scoring result records its params "
                     "(results saved before params were recorded don't); name it, or re-score the run")


def check_comparable(
    before: RunReport, after: RunReport, *, before_subject: str | None = None, after_subject: str | None = None,
) -> Comparability:
    """Whether `after` (say, an enriched snapshot's run) can be compared with `before` (the baseline of record).

    Each run's subject - the taxonomy it scores - is read from the report unless given (`report_subject`); a run
    scoring several taxonomies needs it named. Mismatches are reported, not raised: the result lists every issue.
    It fails closed: a subject that no result of its run scores (a typo, say) is an issue, not an empty pass.
    """
    subjects = {"before": _subject(before, before_subject), "after": _subject(after, after_subject)}
    scored = {
        label: _scored(report, subjects[label]) if subjects[label] is not None else {}
        for label, report in (("before", before), ("after", after))
    }
    counts = {
        "before": {dataset.name: dataset.counts for dataset in before.datasets},
        "after": {dataset.name: dataset.counts for dataset in after.datasets},
    }
    metrics = [
        comparison
        for metric_id in dict.fromkeys([*scored["before"], *scored["after"]])
        for comparison in _compare_metric(
            metric_id, scored["before"].get(metric_id, []), scored["after"].get(metric_id, []), subjects, counts,
        )
    ]
    return Comparability(
        before_run_id=before.run_id, after_run_id=after.run_id,
        before_subject=subjects["before"], after_subject=subjects["after"],
        before_definitions_version=before.metric_definitions_version,
        after_definitions_version=after.metric_definitions_version,
        issues=[*_run_issues(before, after), *_subject_issues(before, after, subjects, scored)], metrics=metrics,
    )


def _subject(report: RunReport, named: str | None) -> str | None:
    """The named subject, else the report's; None for a run that didn't finish, which `_run_issues` reports."""
    if named:
        return named
    try:
        return report_subject(report)
    except ValueError:
        if report.status != "done":
            return None
        raise


def _subject_issues(
    before: RunReport, after: RunReport, subjects: dict[str, str | None], scored: dict[str, dict[str, list[_Scored]]],
) -> list[ComparabilityIssue]:
    """A finished run with no result scoring its subject: nothing would be compared, so it mustn't pass.

    Results without recorded params can't be matched to a subject at all; `_run_issues` already flags those.
    """
    return [
        ComparabilityIssue(kind="missing_result", message=(
            f"the {label} run has no scoring result for {subjects[label]!r}; check the subject is the taxonomy "
            "that run scores"
        ))
        for label, report in (("before", before), ("after", after))
        if report.status == "done" and subjects[label] is not None and not scored[label]
        and not any(result.params is None and _subject_param(result.metric_id) for result in report.results)
    ]


def _run_issues(before: RunReport, after: RunReport) -> list[ComparabilityIssue]:
    """What stops any result of the two runs comparing: a run that didn't finish, or unknown or different scoring."""
    issues = []
    for label, report in (("before", before), ("after", after)):
        if report.status != "done":
            issues.append(ComparabilityIssue(kind="run_status", message=(
                f"the {label} run is {report.status}" + (f": {report.error}" if report.error else "")
            )))
        if report.metric_definitions_version is None:
            issues.append(ComparabilityIssue(kind="definitions_version", message=(
                f"the {label} run records no metric definitions version (it was saved before runs were stamped), "
                "so the formulas it was scored with are unknown; re-score it"
            )))
        unrecorded = sum(result.params is None and _subject_param(result.metric_id) is not None
                         for result in report.results)
        if unrecorded:
            issues.append(ComparabilityIssue(kind="settings", message=(
                f"{unrecorded} scoring result(s) in the {label} run record no params (saved before results recorded "
                "them), so what they scored and with which settings is unknown; re-score it"
            )))
    versions = before.metric_definitions_version, after.metric_definitions_version
    if None not in versions and versions[0] != versions[1]:
        issues.append(ComparabilityIssue(kind="definitions_version", message=(
            f"the before run was scored with metric definitions v{versions[0]} and the after run with "
            f"v{versions[1]}: formulas or defaults changed in between, so even results with the same settings "
            "measure different things; re-score one so both use the same version"
        )))
    return issues


def _scored(report: RunReport, subject: str) -> dict[str, list[_Scored]]:
    """The run's results scoring `subject`, by metric id, in report order."""
    scored: dict[str, list[_Scored]] = {}
    for index, result in enumerate(report.results):
        param = _subject_param(result.metric_id)
        if param is None or result.params is None or result.params.get(param) != subject:
            continue
        settings = {key: value for key, value in result.params.items() if key != param}
        scored.setdefault(result.metric_id, []).append(_Scored(index, result, settings))
    return scored


def _compare_metric(
    metric_id: str, before: list[_Scored], after: list[_Scored],
    subjects: dict[str, str], counts: dict[str, dict[str, dict[str, int]]],
) -> list[MetricComparison]:
    """Pair this metric's results that share their settings, and say why the rest don't pair."""
    results = {"before": before, "after": after}
    unmatched: dict[str, list[_Scored]] = {"before": list(before), "after": []}
    comparisons = []
    for item in after:
        match = next((other for other in unmatched["before"] if other.settings == item.settings), None)
        if match is None:
            unmatched["after"].append(item)
            continue
        unmatched["before"].remove(match)
        comparisons.append(MetricComparison(
            metric_id=metric_id, before_index=match.index, after_index=item.index,
            issues=_pair_issues(match, item, counts),
        ))
    if len(unmatched["before"]) == len(unmatched["after"]) == 1:
        # One result each that didn't pair: the same measurement taken with different settings.
        old, new = unmatched["before"][0], unmatched["after"][0]
        comparisons.append(MetricComparison(
            metric_id=metric_id, before_index=old.index, after_index=new.index,
            issues=[_settings_issue(old.settings, new.settings)],
        ))
        return comparisons
    for label, other in (("before", "after"), ("after", "before")):
        missing = "result with the same settings" if results[other] else "result"
        for item in unmatched[label]:
            comparisons.append(MetricComparison(
                metric_id=metric_id, **{f"{label}_index": item.index},
                issues=[ComparabilityIssue(kind="missing_result", message=(
                    f"the {other} run has no {metric_id} {missing} for {subjects[other]}"
                ))],
            ))
    return comparisons


def _pair_issues(
    before: _Scored, after: _Scored, counts: dict[str, dict[str, dict[str, int]]],
) -> list[ComparabilityIssue]:
    issues = [
        ComparabilityIssue(kind="result_status", message=f"the {label} result is {item.result.status}: "
                           f"{item.result.summary}")
        for label, item in (("before", before), ("after", after)) if item.result.status != "ok"
    ]
    # Datasets the settings name (reference workbooks, incoming sources) must hold the same data in both runs.
    for name in _dataset_names(before.settings, counts["before"].keys() & counts["after"].keys()):
        old, new = counts["before"][name], counts["after"][name]
        if old != new:
            changed = ", ".join(
                f"{key} {old.get(key)} -> {new.get(key)}" for key in sorted(old.keys() | new.keys())
                if old.get(key) != new.get(key)
            )
            issues.append(ComparabilityIssue(kind="reference_data", message=(
                f"{name}, which this result reads, has different row counts in the two runs ({changed})"
            )))
    return issues


def _dataset_names(settings: dict[str, Any], loaded: set[str]) -> list[str]:
    names = {
        name for value in settings.values() for name in (value if isinstance(value, list) else [value])
        if isinstance(name, str) and name in loaded
    }
    return sorted(names)


_UNSET = object()


def _settings_issue(before: dict[str, Any], after: dict[str, Any]) -> ComparabilityIssue:
    def text(value: Any) -> str:
        return "unset" if value is _UNSET else json.dumps(value, sort_keys=True)

    changes = [
        f"{key} {text(before.get(key, _UNSET))} -> {text(after.get(key, _UNSET))}"
        for key in sorted(before.keys() | after.keys()) if before.get(key, _UNSET) != after.get(key, _UNSET)
    ]
    return ComparabilityIssue(kind="settings", message="scored with different settings: " + "; ".join(changes))
