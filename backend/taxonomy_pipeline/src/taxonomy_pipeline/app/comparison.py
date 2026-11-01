"""Comparison use cases: compare two runs, and render the comparison for people to read.

`compare` is the one implementation behind `taxonomy-pipeline compare` and
`GET /runs/{before_id}/compare/{after_id}`. The CLI loads runs from report
files or run history (`compare_runs`); the API only from run history, by ID,
so a request can't name a file on the server. Markdown and CSV exports are
for reviewers and spreadsheets; the dashboard reads the JSON shape
(`RunComparison`) instead.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ..models import MeasureChange, RunComparison, RunReport
from ..quality.comparison import compare_reports
from .scoring import load_report, result_labels

_VERDICT_TEXT = {
    "improved": "improved", "deteriorated": "DETERIORATED", "unchanged": "unchanged", "changed": "changed",
    "not_compared": "not compared",
}


def compare(
    before: RunReport, after: RunReport, *, before_subject: str | None = None, after_subject: str | None = None,
) -> RunComparison:
    """Compare two loaded runs, labelling each result the way `run` and `check-comparable` print it."""
    return compare_reports(
        before, after, before_labels=result_labels(before.results), after_labels=result_labels(after.results),
        before_subject=before_subject, after_subject=after_subject,
    )


def compare_runs(
    before: str, after: str, *, before_subject: str | None = None, after_subject: str | None = None,
) -> RunComparison:
    """Load two runs (report files or run IDs, as `load_report` reads them) and compare them."""
    return compare(load_report(before), load_report(after), before_subject=before_subject, after_subject=after_subject)


FORMATS = ("markdown", "csv", "json")
_SUFFIX_FORMATS = {".md": "markdown", ".markdown": "markdown", ".csv": "csv", ".json": "json"}


def output_format(path: str | Path | None, requested: str | None = None) -> str:
    """The format asked for, else the one the output file's suffix names, else Markdown."""
    if requested:
        if requested not in FORMATS:
            raise ValueError(f"unknown format {requested!r}; choose one of {', '.join(FORMATS)}")
        return requested
    return _SUFFIX_FORMATS.get(Path(path).suffix.lower(), "markdown") if path else "markdown"


def render(comparison: RunComparison, fmt: str) -> str:
    if fmt == "json":
        return comparison.model_dump_json(indent=2) + "\n"
    return to_csv(comparison) if fmt == "csv" else to_markdown(comparison)


def write_comparison(comparison: RunComparison, path: str | Path, fmt: str) -> None:
    Path(path).write_text(render(comparison, fmt), encoding="utf-8")


def to_markdown(comparison: RunComparison) -> str:
    """A report a reviewer can read without opening SQL or raw report JSON (PB10 AC 7)."""
    check = comparison.comparability
    lines = [
        "# Taxonomy quality comparison",
        "",
        f"- **Before:** {_run_text(check.before_run_id, check.before_subject, check.before_definitions_version)}",
        f"- **After:** {_run_text(check.after_run_id, check.after_subject, check.after_definitions_version)}",
        "",
    ]
    if check.comparable:
        lines.append("Both runs were scored alike, so every change below is a change in the taxonomy.")
    else:
        lines.append("**Not comparable.** Changes are only shown for results scored alike; fix these first:")
        lines += [f"- {issue.message}" for issue in check.issues]
    counts = _verdict_counts(comparison)
    if counts:
        lines += ["", "**Summary:** " + ", ".join(f"{count} {_VERDICT_TEXT[verdict]}" for verdict, count in counts)]
    for metric in comparison.metrics:
        lines += ["", f"## {metric.label}", ""]
        lines += [f"- Not compared: {issue.message}" for issue in metric.issues]
        if metric.issues:
            lines.append("")
        if not metric.measures:
            lines.append("_No measures: the result is missing or didn't run._")
            continue
        lines += ["| Measure | Before | After | Change | Relative | Verdict |", "|---|--:|--:|--:|--:|---|"]
        lines += [
            f"| {measure.label} | {_value(measure, measure.before)} | {_value(measure, measure.after)} | "
            f"{_delta(measure)} | {_relative(measure)} | {_VERDICT_TEXT[measure.verdict]} |"
            for measure in metric.measures
        ]
    return "\n".join(lines) + "\n"


def to_csv(comparison: RunComparison) -> str:
    """One row per measure, with raw numbers, for a spreadsheet."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["metric_id", "result", "measure_key", "measure", "better", "before", "after", "change",
                     "relative_change", "verdict"])
    for metric in comparison.metrics:
        for measure in metric.measures:
            writer.writerow([
                metric.metric_id, metric.label, measure.key, measure.label, measure.better or "",
                *("" if value is None else value for value in (
                    measure.before, measure.after, measure.change, measure.relative_change,
                )),
                measure.verdict,
            ])
    return out.getvalue()


def _verdict_counts(comparison: RunComparison) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for metric in comparison.metrics:
        for measure in metric.measures:
            counts[measure.verdict] = counts.get(measure.verdict, 0) + 1
    return [(verdict, counts[verdict]) for verdict in _VERDICT_TEXT if verdict in counts]


def _run_text(run_id: str, subject: str | None, version: int | None) -> str:
    definitions = f"definitions v{version}" if version is not None else "definitions unknown"
    return f"run `{run_id}` ({subject or 'no scored taxonomy'}, {definitions})"


def _is_rate(measure: MeasureChange) -> bool:
    return measure.key.endswith(("rate", "coverage")) or "rate_" in measure.key


def _number(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _value(measure: MeasureChange, value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1%}" if _is_rate(measure) else _number(value)


def _delta(measure: MeasureChange) -> str:
    if measure.change is None:
        return "-"
    if _is_rate(measure):
        return f"{measure.change * 100:+.1f} pp"
    return ("+" if measure.change > 0 else "") + _number(measure.change)


def _relative(measure: MeasureChange) -> str:
    return "-" if measure.relative_change is None else f"{measure.relative_change:+.1%}"
