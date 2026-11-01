"""Comparison use cases: loading two runs, and the Markdown/CSV/JSON a reviewer reads."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from taxonomy_pipeline.app import comparison
from taxonomy_pipeline.app.scoring import write_report
from taxonomy_pipeline.models import RunComparison
from tests.conftest import baseline_of_record
from tests.conftest import enriched_report as _enriched


def _saved(tmp_path: Path, report, name: str) -> str:
    path = tmp_path / name
    write_report(report, path)
    return str(path)


def test_compare_runs_loads_report_files_and_labels_results_like_run_does(tmp_path: Path) -> None:
    before = _saved(tmp_path, baseline_of_record(), "baseline.json.gz")
    after = _saved(tmp_path, _enriched(**{"breadth__occupational.matched_count": 24}), "enriched.json")

    result = comparison.compare_runs(before, after)

    assert result.comparability.comparable
    assert [metric.label for metric in result.metrics] == ["integrity", "depth", "breadth", "saturation"]


def test_markdown_states_both_runs_summarises_verdicts_and_tabulates_each_metric() -> None:
    result = comparison.compare(baseline_of_record(), _enriched(**{"breadth__occupational.matched_count": 24,
                                                                   "breadth__occupational.coverage": 0.166}))

    text = comparison.to_markdown(result)

    assert "**Before:** run `3d685fb6b1aa4a99b60d256700b4d256` (original, definitions v1)" in text
    assert "**After:** run `enriched-run` (enriched, definitions v1)" in text
    assert "**Summary:** 2 improved, 29 unchanged" in text
    assert "| SSOC occupational nodes covered | 16 | 24 | +8 | +50.0% | improved |" in text
    assert "| Occupational coverage | 11.0% | 16.6% | +5.6 pp | +50.9% | improved |" in text


def test_markdown_puts_whats_not_comparable_first_and_shows_no_changes() -> None:
    after = _enriched()
    after.metric_definitions_version = 2

    text = comparison.to_markdown(comparison.compare(baseline_of_record(), after))

    assert "**Not comparable.**" in text and "definitions v1 and the after run with v2" in text
    assert "| Dead-end categories | 14 | 14 | - | - | not compared |" in text


def test_markdown_marks_a_deterioration_so_it_stands_out() -> None:
    after = _enriched(**{"integrity__orphans.dangling_speciality_tag_links": 3})

    text = comparison.to_markdown(comparison.compare(baseline_of_record(), after))

    assert "| Dangling speciality-tag links | 0 | 3 | +3 | - | DETERIORATED |" in text


def test_csv_has_one_row_per_measure_with_raw_numbers() -> None:
    result = comparison.compare(baseline_of_record(), _enriched(**{"breadth__occupational.matched_count": 24}))

    rows = list(csv.DictReader(io.StringIO(comparison.to_csv(result))))

    assert len(rows) == sum(len(metric.measures) for metric in result.metrics)
    row = next(row for row in rows if row["measure_key"] == "occupational.matched_count")
    assert (row["metric_id"], row["before"], row["after"], row["change"], row["verdict"]) == (
        "breadth", "16.0", "24.0", "8.0", "improved",
    )
    assert next(row for row in rows if row["measure_key"] == "reviewed")["better"] == ""


def test_json_round_trips_to_the_shared_model() -> None:
    result = comparison.compare(baseline_of_record(), _enriched())

    assert RunComparison.model_validate(json.loads(comparison.render(result, "json"))) == result


@pytest.mark.parametrize(("path", "requested", "expected"), [
    (None, None, "markdown"), ("out.csv", None, "csv"), ("out.JSON", None, "json"), ("out.md", None, "markdown"),
    ("out.txt", None, "markdown"), ("out.csv", "json", "json"),
])
def test_the_output_format_is_the_one_asked_for_else_the_files_suffix(path, requested, expected) -> None:
    assert comparison.output_format(path, requested) == expected


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown format 'pdf'"):
        comparison.output_format(None, "pdf")
