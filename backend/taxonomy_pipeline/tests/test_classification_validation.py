"""Exercise intake independently of upstream comparison and unfinished producer code."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from taxonomy_pipeline.enrichment.models import BatchValidationReport, ClassificationOutput
from taxonomy_pipeline.enrichment.validation import validate_outputs
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset
from taxonomy_pipeline.taxonomy.sql_dump import SqlDumpSource

FIXTURES = Path(__file__).parent / "fixtures" / "classification"


# Read synthetic producer files without depending on local reference exports.
def load_rows(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
# Supply a fresh taxonomy snapshot to isolate every test's mutations.
def taxonomy() -> TaxonomyDataset:
    return TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))


# Accept both branches of the latest contract while retaining distinct source identities and placements.
def test_current_contract_batches_merge_without_changing_the_taxonomy(taxonomy: TaxonomyDataset) -> None:
    before = taxonomy.model_dump()
    report = validate_outputs({name: json.dumps(load_rows(name)) for name in ("case_a.json", "case_b.json")}, taxonomy)
    assert report.complete
    assert report.counts["valid"] == 8
    assert len(report.merged_outputs) == 8
    assert report.records[0].target.model_dump() == dict(service_id=1, category_id=10, speciality_id=120)
    assert taxonomy.model_dump() == before
    assert BatchValidationReport.model_validate_json(report.model_dump_json()) == report
    assert sum(report.counts.values()) == 2 * report.counts["received"]


def test_empty_input_reports_zero_counts_and_rates(taxonomy: TaxonomyDataset) -> None:
    report = validate_outputs({}, taxonomy)

    assert report.counts == dict(received=0, valid=0, duplicate=0, unresolved=0, review=0, invalid=0, skipped=0)
    assert report.metrics == {
        "total_records": 0,
        "valid_records": 0,
        "review_records": 0,
        "unresolved_records": 0,
        "duplicate_records": 0,
        "invalid_records": 0,
        "skipped_records": 0,
        "valid_rate": 0.0,
        "review_rate": 0.0,
    }


# Old eight-field files are rejected row by row rather than adapted into the contract.
def test_previous_format_files_are_invalid(taxonomy: TaxonomyDataset) -> None:
    report = validate_outputs({"a": json.dumps(load_rows("previous_format.json"))}, taxonomy)
    assert report.counts["invalid"] == 2
    assert report.merged_outputs == []


@pytest.mark.parametrize("field,value", [("confidence", "0.8"), ("category", None), ("tag_id", 42)])
# Malformed rows stay visible without discarding the rest of their batch.
def test_malformed_rows_preserve_raw_rows(taxonomy: TaxonomyDataset, field: str, value: Any) -> None:
    good = load_rows("case_b.json")[0]
    bad = {**good, field: value}
    report = validate_outputs({"b": json.dumps([bad, good])}, taxonomy)
    assert [row.status for row in report.records] == ["invalid", "valid"]
    assert report.records[0].raw == bad


@pytest.mark.parametrize("filename", ["case_a.json", "case_b.json"])
# Retain extra input evidence without changing normalized outputs or repeated-placement detection.
def test_extra_fields_stay_in_raw_evidence_only(taxonomy: TaxonomyDataset, filename: str) -> None:
    original = load_rows(filename)[0]
    extras = {"metadata": {"scores": [0.8, None]}, "extra_flag": True, "extra_note": None}
    enriched = {**original, **extras}
    report = validate_outputs({filename: json.dumps([enriched, original])}, taxonomy)
    first, second = report.records
    assert [first.status, second.status] == ["valid", "duplicate"]
    assert first.raw == enriched
    assert first.output == second.output
    assert first.candidate == second.candidate == first.output.model_dump()
    # needs_review is optional and omitted when false, so the candidate keeps the nine contract fields.
    assert set(first.candidate) == set(ClassificationOutput.model_fields) - {"needs_review"}
    assert report.merged_outputs == [first.output]
    serialized = json.loads(report.model_dump_json())
    assert serialized["records"][0]["raw"] == enriched
    assert serialized["merged_outputs"] == [first.output.model_dump()]
    assert "metadata" not in serialized["records"][0]["output"]


# Treat names under different parents as new placements without moving existing nodes.
def test_missing_paths_are_valid_additions_under_the_supplied_parents(taxonomy: TaxonomyDataset) -> None:
    before = taxonomy.model_dump()
    report = validate_outputs({"current": json.dumps(load_rows("invalid_placements.json"))}, taxonomy)
    assert report.counts["valid"] == 3
    assert report.records[0].target.model_dump() == dict(service_id=1, category_id=None, speciality_id=None)
    assert report.records[1].target.model_dump() == dict(service_id=1, category_id=10, speciality_id=None)
    assert report.records[2].target.model_dump() == dict(service_id=3, category_id=None, speciality_id=None)
    assert taxonomy.model_dump() == before


@pytest.mark.parametrize("tier", ["services", "categories", "specialities"])
# Keep same-parent ambiguity as review instead of guessing which existing node to reuse.
def test_ambiguous_existing_nodes_require_review(taxonomy: TaxonomyDataset, tier: str) -> None:
    rows = getattr(taxonomy, tier)
    rows.append({**rows[0], "id": 999})
    row = load_rows("case_a.json")[0]
    report = validate_outputs({"current": json.dumps([row])}, taxonomy)
    assert report.records[0].status == "review"
    assert report.records[0].findings[0].startswith("ambiguous_path:")
    assert report.records[0].output.model_dump() == row
    assert report.merged_outputs == []


# A category remains ambiguous when adding a speciality that cannot disambiguate its parents.
def test_new_speciality_does_not_hide_ambiguous_category(taxonomy: TaxonomyDataset) -> None:
    taxonomy.categories.append(dict(id=30, name="Data analytics", service_id=1))
    row = {**load_rows("case_a.json")[0], "speciality": "New speciality"}
    report = validate_outputs({"current": json.dumps([row])}, taxonomy)
    assert report.counts["review"] == 1
    assert report.records[0].target is None


# An unresolved output keeps every placement field null and its raw row as evidence.
def test_unresolved_outputs_keep_null_placements(taxonomy: TaxonomyDataset) -> None:
    row = load_rows("unresolved.json")[0]
    report = validate_outputs({"current": json.dumps([row])}, taxonomy)
    assert report.records[0].status == "unresolved"
    assert report.records[0].output.service is None
    assert report.records[0].output.original_classification == "unclassified"
    assert report.records[0].raw == row


# Missing top-level services are taxonomy defects rather than permission to create arbitrary services.
def test_a_missing_service_requires_review(taxonomy: TaxonomyDataset) -> None:
    row = load_rows("case_a.json")[0]
    taxonomy.services = [service for service in taxonomy.services if service["name"] != row["service"]]
    report = validate_outputs({"current": json.dumps([row])}, taxonomy)
    assert report.counts["review"] == 1
    assert any(item.startswith("missing_service:") for item in report.records[0].findings)
    assert report.records[0].output.service == row["service"]
    assert report.merged_outputs == []


# Collapse repeated new paths without treating null database IDs as a shared identity.
def test_new_placement_deduplication_uses_the_full_parent_path(taxonomy: TaxonomyDataset) -> None:
    row = {**load_rows("case_a.json")[0], "category": "New category", "speciality": "New speciality"}
    repeated = {**row, "category": " NEW category! ", "confidence": 0.5}
    other_category = {**row, "category": "Another category"}
    other_service = {**row, "service": "Industry"}
    other_speciality = {**row, "speciality": "Another speciality"}
    rows = [row, repeated, other_category, other_service, other_speciality]
    report = validate_outputs({"current": json.dumps(rows)}, taxonomy)
    assert [record.status for record in report.records] == ["valid", "duplicate", "valid", "valid", "valid"]
    assert report.records[1].duplicate_of == report.records[0].origin
    assert all(record.target.category_id is None for record in report.records)
    assert len(report.merged_outputs) == 4


@pytest.mark.parametrize("problem", ["duplicate_id", "missing_service", "boolean_parent", "invalid_id", "bad_service"])
# Do not emit usable IDs from a malformed taxonomy snapshot.
def test_invalid_taxonomy_paths_require_review(taxonomy: TaxonomyDataset, problem: str) -> None:
    if problem == "duplicate_id":
        taxonomy.specialities.append({**taxonomy.specialities[0], "name": "Other"})
    elif problem == "missing_service":
        taxonomy.services.clear()
    elif problem == "boolean_parent":
        taxonomy.categories[0]["service_id"] = True
    elif problem == "invalid_id":
        taxonomy.specialities[0]["id"] = -1
    else:
        taxonomy.services[0]["name"] = "Unknown"
    row = load_rows("case_a.json")[0]
    report = validate_outputs({"current": json.dumps([row])}, taxonomy)
    assert report.records[0].status == "review"
    assert report.merged_outputs == []


# Normalize only lookup names while preserving source text and producer path spelling.
def test_lookup_normalization_preserves_output_text(taxonomy: TaxonomyDataset) -> None:
    row = load_rows("case_a.json")[0]
    row.update(tag="  DATA Visualization!  ", category=" DATA analytics ", speciality="data presentation!")
    report = validate_outputs({"current": json.dumps([row])}, taxonomy)
    assert report.records[0].status == "valid"
    assert report.merged_outputs[0].model_dump() == row


# Collapse repeated source placements without combining different tags or losing score evidence.
def test_repeated_placements_retain_origins_and_different_confidence(taxonomy: TaxonomyDataset) -> None:
    row = load_rows("case_a.json")[0]
    repeat = {**row, "confidence": 0.5, "reason": "A second proposal."}
    other_source = {**row, "source_dataset": "another"}
    report = validate_outputs({"current": json.dumps([row, repeat, other_source])}, taxonomy)
    assert [record.status for record in report.records] == ["valid", "duplicate", "valid"]
    assert report.records[1].duplicate_of == report.records[0].origin
    assert report.records[1].raw == repeat
    assert len(report.merged_outputs) == 2


# Retain identical unresolved records in the audit report while collapsing their repeated processing.
def test_identical_unresolved_rows_are_accounted_for(taxonomy: TaxonomyDataset) -> None:
    row = load_rows("unresolved.json")[0]
    report = validate_outputs({"current": json.dumps([row, row])}, taxonomy)
    assert [record.status for record in report.records] == ["unresolved", "duplicate"]
    assert report.merged_outputs == []


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("field,value", [("tag", "Another concept"), ("original_classification", "unclassified")])
# Detect source conflicts before duplicate reduction regardless of input order or path validity.
def test_source_conflicts_hold_all_records(taxonomy: TaxonomyDataset, field: str, value: str, reverse: bool) -> None:
    row = load_rows("case_a.json")[0]
    conflict = {**row, field: value, "category": "Missing"}
    rows = [row, deepcopy(row), conflict]
    report = validate_outputs({"current": json.dumps(rows[::-1] if reverse else rows)}, taxonomy)
    assert report.counts["review"] == 3
    assert report.counts["duplicate"] == 0
    assert report.merged_outputs == []


@pytest.mark.parametrize("bad", ['[{', '{}', '[{"tag_id":"1","tag_id":"2"}]', '[NaN]', '[1e999]', b'\xff'])
# Reject unreadable or ambiguous JSON at file level while continuing with other files.
def test_bad_files_do_not_discard_readable_batches(taxonomy: TaxonomyDataset, bad: str | bytes) -> None:
    report = validate_outputs({"bad": bad, "good": json.dumps(load_rows("case_a.json"))}, taxonomy)
    assert not report.complete
    assert set(report.input_errors) == {"bad"}
    assert report.counts["valid"] == 4


# Reject malformed array elements individually and retain their zero-based input positions.
def test_non_object_rows_do_not_discard_good_records(taxonomy: TaxonomyDataset) -> None:
    row = load_rows("case_a.json")[0]
    report = validate_outputs({"current": json.dumps([None, [], row])}, taxonomy)
    assert report.counts["invalid"] == 2
    assert report.records[2].origin.row_index == 2
    assert report.counts["valid"] == 1


# Exercise SQL loading and intake together without a database or upstream matching result.
def test_sql_taxonomy_integration(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.sql"
    path.write_text(
        "INSERT INTO services (id,name) VALUES (1,'Subject');\n"
        "INSERT INTO categories (id,name,service_id) VALUES (10,'Data analytics',1);\n"
        "INSERT INTO specialities (id,name,category_id) VALUES (120,'Data presentation',10);\n",
        encoding="utf-8",
    )
    taxonomy = SqlDumpSource("sql-fixture", [path]).load()
    row = load_rows("case_a.json")[0]
    report = validate_outputs({"a": json.dumps([row])}, taxonomy)
    assert report.counts["valid"] == 1
    assert report.records[0].target.speciality_id == 120
    assert ClassificationOutput.model_validate_json(report.merged_outputs[0].model_dump_json())
