"""Check portable CSV review output, including Unicode, quoting, and formula-like source text."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from taxonomy_pipeline.classification.export import export_preview_csv
from taxonomy_pipeline.classification.models import MergePreview, PreviewRow, ValidationReport
from taxonomy_pipeline.datasets.base import TaxonomyDataset


# Supply a minimal shared preview containing rows from the builder's public export shape.
def preview(rows: list[PreviewRow]) -> MergePreview:
    return MergePreview(
        taxonomy=TaxonomyDataset(name="snapshot"), baseline_snapshot_id="example-snapshot",
        validation=ValidationReport(), rows=rows,
    )


# Round-trip commas, quotes, line breaks, Unicode, scores, and identifiers through standard CSV readers.
def test_csv_retains_review_fields_and_special_characters(tmp_path: Path) -> None:
    row = PreviewRow(
        status="proposed", tag='Analysis, "data"\nand café', source_dataset="供应商", tag_id="00042",
        service="Industry", category="New category", speciality="New speciality", confidence=0.75,
        canonical_tag_id="new:tag:abc", category_change="new", speciality_change="new", link_change="new",
    )
    output = export_preview_csv(preview([row]), tmp_path / "nested" / "preview.csv")
    with output.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        values = list(reader)
        assert reader.fieldnames == list(PreviewRow.model_fields)
    assert values[0]["tag"] == row.tag and values[0]["source_dataset"] == row.source_dataset
    assert values[0]["tag_id"] == "00042" and values[0]["confidence"] == "0.75"
    assert values[0]["category_id"] == "" and values[0]["canonical_tag_id"] == "new:tag:abc"


@pytest.mark.parametrize("text", ["=1+1", "+CMD()", "-1+2", "@SUM(A1)", "\t=1", " \n+1"])
# Spreadsheet formula-like text is escaped without changing the underlying preview evidence.
def test_csv_escapes_formula_like_text(tmp_path: Path, text: str) -> None:
    report = preview([PreviewRow(status="pending", tag=text, reason=text)])
    output = export_preview_csv(report, tmp_path / "preview.csv")
    with output.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["tag"] == row["reason"] == "'" + text
    assert report.rows[0].tag == text


# Export summary, omission, and blocking-check rows in the same file without mixing their counts with placements.
def test_csv_exports_partial_and_blocked_findings(tmp_path: Path) -> None:
    report = preview([
        PreviewRow(row_type="summary", status="blocked", note="added_tags", count=0),
        PreviewRow(row_type="omission", status="unresolved", count=5, note="See validation.json"),
        PreviewRow(row_type="check", status="introduced.duplicate_id", note="Collision"),
    ])
    output = export_preview_csv(report, tmp_path / "preview.csv")
    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["row_type"] for row in rows] == ["summary", "omission", "check"]
    assert [row["count"] for row in rows] == ["0", "5", ""]


# Re-exporting to the same path replaces the file without appending duplicate review rows.
def test_repeated_exports_have_identical_bytes_and_do_not_append(tmp_path: Path) -> None:
    report = preview([PreviewRow(status="pending", tag="Example")])
    output = export_preview_csv(report, tmp_path / "preview.csv")
    before = output.read_bytes()
    export_preview_csv(report, output)
    assert output.read_bytes() == before
