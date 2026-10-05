"""Exercise intake independently of upstream comparison and unfinished producer code."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from taxonomy_pipeline.classification.models import BatchValidationReport, ClassificationOutput
from taxonomy_pipeline.classification.validation import validate_outputs
from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.datasets.sql_dump import SqlDumpSource

FIXTURES = Path(__file__).parent / "fixtures" / "classification"


# Read synthetic producer files without depending on local reference exports.
def load_rows(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
# Supply a fresh taxonomy snapshot to isolate every test's mutations.
def taxonomy() -> TaxonomyDataset:
    return TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))


# Skip only the known Case A handoff and let the corresponding Case B record proceed.
def test_legacy_batches_merge_without_handoff_conflicts(taxonomy: TaxonomyDataset) -> None:
    before = taxonomy.model_dump()
    a, b = load_rows("legacy_case_a.json"), load_rows("legacy_case_b.json")
    report = validate_outputs(
        {"a": json.dumps(a), "b": json.dumps(b)}, taxonomy,
        case_a_inputs={"a"}, case_b_sources={"b": "skillsfuture"}, skip_case_a_handoffs=True,
    )
    assert report.complete
    assert report.counts == dict(received=4, valid=3, skipped=1, review=0, duplicate=0, unresolved=0, invalid=0)
    assert [row.tag_id for row in report.merged_outputs] == ["42", "48", "49"]
    assert all(row.service == "Industry" for row in report.merged_outputs)
    assert report.records[1].raw == a[1]
    assert report.records[1].output is None
    assert report.records[2].raw == b[0]
    assert report.records[2].output.original_classification == "unclassified"
    assert report.records[3].candidate["category"] == "Quantum science"
    assert report.records[3].output.category == "Quantum science"
    assert report.records[3].target.model_dump() == dict(service_id=2, category_id=None, speciality_id=None)
    assert any(item.startswith("new_category:") for item in report.records[3].findings)
    assert taxonomy.model_dump() == before
    assert BatchValidationReport.model_validate_json(report.model_dump_json()) == report
    assert sum(report.counts.values()) == 2 * report.counts["received"]


# Keep handoff skipping opt-in, since producer flags must ordinarily remain review evidence.
def test_handoffs_conflict_with_case_b_unless_explicitly_skipped(taxonomy: TaxonomyDataset) -> None:
    report = validate_outputs(
        {"a": json.dumps(load_rows("legacy_case_a.json")), "b": json.dumps(load_rows("legacy_case_b.json"))},
        taxonomy, case_a_inputs={"a"}, case_b_sources={"b": "skillsfuture"},
    )
    for index in (1, 2):
        assert report.records[index].status == "review"
        assert any(item.startswith("conflicting_original_classification:") for item in report.records[index].findings)


# A null placement without a route instruction remains unresolved rather than disappearing.
def test_other_case_a_unresolved_rows_are_preserved(taxonomy: TaxonomyDataset) -> None:
    row = load_rows("legacy_case_a.json")[1]
    row["reason"] = "Needs review; uncertain classification."
    report = validate_outputs({"a": json.dumps([row])}, taxonomy, case_a_inputs={"a"}, skip_case_a_handoffs=True)
    assert report.records[0].status == "unresolved"
    assert report.records[0].output.service is None
    assert report.records[0].output.original_classification == "classified"


# Restrict the temporary skip rule to legacy Case A records with no placement.
def test_handoff_text_does_not_skip_populated_or_current_contract_rows(taxonomy: TaxonomyDataset) -> None:
    legacy = load_rows("legacy_case_a.json")[0]
    current = load_rows("unresolved.json")[0]
    legacy["reason"] = current["reason"] = "route to Case B"
    report = validate_outputs(
        {"a": json.dumps([legacy]), "current": json.dumps([current])}, taxonomy,
        case_a_inputs={"a"}, skip_case_a_handoffs=True,
    )
    assert [row.status for row in report.records] == ["valid", "unresolved"]


# Accept both branches of the latest contract while retaining distinct source identities and placements.
def test_current_contract_batches_need_no_adapter(taxonomy: TaxonomyDataset) -> None:
    report = validate_outputs({name: json.dumps(load_rows(name)) for name in ("case_a.json", "case_b.json")}, taxonomy)
    assert report.counts["valid"] == 8
    assert len(report.merged_outputs) == 8
    assert report.records[0].target.model_dump() == dict(service_id=1, category_id=10, speciality_id=120)


# An explicit adapter is required so old files cannot silently masquerade as the latest contract.
def test_legacy_files_without_adapter_are_invalid(taxonomy: TaxonomyDataset) -> None:
    report = validate_outputs({"a": json.dumps(load_rows("legacy_case_a.json"))}, taxonomy)
    assert report.counts["invalid"] == 2
    assert report.merged_outputs == []


@pytest.mark.parametrize("field,value", [("confidence", "0.8"), ("category", None), ("tag_id", 42)])
# Malformed legacy rows stay visible without discarding the rest of their batch.
def test_adapter_errors_preserve_raw_rows(taxonomy: TaxonomyDataset, field: str, value: Any) -> None:
    good = load_rows("legacy_case_b.json")[0]
    bad = {**good, field: value}
    report = validate_outputs({"b": json.dumps([bad, good])}, taxonomy, case_b_sources={"b": "skillsfuture"})
    assert [row.status for row in report.records] == ["invalid", "valid"]
    assert report.records[0].raw == bad


@pytest.mark.parametrize("filename", ["case_a.json", "case_b.json", "legacy_case_a.json", "legacy_case_b.json"])
# Retain extra input evidence without changing normalized outputs or repeated-placement detection.
def test_extra_fields_stay_in_raw_evidence_only(taxonomy: TaxonomyDataset, filename: str) -> None:
    original = load_rows(filename)[0]
    extras = {"metadata": {"scores": [0.8, None]}, "extra_flag": True, "extra_note": None}
    case_a = {filename} if filename == "legacy_case_a.json" else set()
    case_b = {filename: "skillsfuture"} if filename == "legacy_case_b.json" else {}
    if case_a:
        extras.update(tag="Ignored alias", service="Subject")
    if case_b:
        extras.update(source_dataset="Ignored dataset", original_classification="classified", service="Subject")
    enriched = {**original, **extras}
    report = validate_outputs(
        {filename: json.dumps([enriched, original])}, taxonomy,
        case_a_inputs=case_a, case_b_sources=case_b,
    )
    first, second = report.records
    assert [first.status, second.status] == ["valid", "duplicate"]
    assert first.raw == enriched
    assert first.output == second.output
    assert first.candidate == second.candidate == first.output.model_dump()
    assert set(first.candidate) == set(ClassificationOutput.model_fields)
    assert report.merged_outputs == [first.output]
    serialized = json.loads(report.model_dump_json())
    assert serialized["records"][0]["raw"] == enriched
    assert serialized["merged_outputs"] == [first.output.model_dump()]
    assert "metadata" not in serialized["records"][0]["output"]


@pytest.mark.parametrize("filename", ["legacy_case_a.json", "legacy_case_b.json"])
# Require legacy fields even when ignored keys or current-contract aliases are supplied.
def test_legacy_extra_fields_cannot_replace_required_fields(taxonomy: TaxonomyDataset, filename: str) -> None:
    row = load_rows(filename)[0]
    case_a = filename == "legacy_case_a.json"
    del row["original_tag" if case_a else "action"]
    row.update(metadata={"action": "assign_existing"}, service="Industry")
    if case_a:
        row["tag"] = "Cannot replace original_tag"
    report = validate_outputs(
        {filename: json.dumps([row])}, taxonomy,
        case_a_inputs={filename} if case_a else (),
        case_b_sources={} if case_a else {filename: "skillsfuture"},
    )
    assert report.records[0].status == "invalid"
    assert report.records[0].raw == row
    assert report.merged_outputs == []


@pytest.mark.parametrize("action", ["propose_new_category", "propose_new_speciality", "unsupported"])
# Allow new hierarchy proposals while preserving the producer's action as evidence.
def test_case_b_actions_are_preserved_and_enforced(taxonomy: TaxonomyDataset, action: str) -> None:
    row = {**load_rows("legacy_case_b.json")[0], "action": action}
    report = validate_outputs({"b": json.dumps([row])}, taxonomy, case_b_sources={"b": "skillsfuture"})
    assert report.records[0].status == ("invalid" if action == "unsupported" else "valid")
    assert report.records[0].raw["action"] == action
    assert len(report.merged_outputs) == (0 if action == "unsupported" else 1)


# Treat names under different parents as new placements without moving existing nodes.
def test_missing_paths_are_valid_additions_under_the_supplied_parents(taxonomy: TaxonomyDataset) -> None:
    before = taxonomy.model_dump()
    report = validate_outputs({"current": json.dumps(load_rows("invalid_placements.json"))}, taxonomy)
    assert report.counts["valid"] == 3
    assert report.records[0].target.model_dump() == dict(service_id=1, category_id=None, speciality_id=None)
    assert report.records[1].target.model_dump() == dict(service_id=1, category_id=10, speciality_id=None)
    assert report.records[2].target.model_dump() == dict(service_id=3, category_id=None, speciality_id=None)
    assert taxonomy.model_dump() == before


# Default legacy placements to Industry even when their names already form a complete Subject path.
def test_legacy_service_default_does_not_move_existing_paths(taxonomy: TaxonomyDataset) -> None:
    row = load_rows("legacy_case_a.json")[0]
    first = validate_outputs({"a": json.dumps([row])}, taxonomy, case_a_inputs={"a"})
    assert first.records[0].output.service == "Industry"
    assert first.records[0].target.category_id is None
    assert first.records[0].raw == row
    assert first.records[0].findings[0].startswith("temporary_service:")
    taxonomy.categories.append(dict(id=30, name="Data analytics", service_id=2))
    taxonomy.specialities.append(dict(id=300, name="Data presentation", category_id=30))
    reused = validate_outputs({"a": json.dumps([row])}, taxonomy, case_a_inputs={"a"})
    assert reused.records[0].target.model_dump() == dict(service_id=2, category_id=30, speciality_id=300)
    current = load_rows("case_a.json")[0]
    explicit = validate_outputs({"current": json.dumps([current])}, taxonomy)
    assert explicit.records[0].output.service == "Subject"
    assert explicit.records[0].target.speciality_id == 120


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


@pytest.mark.parametrize("action", ["assign_existing", "propose_new_category", "propose_new_speciality"])
# Keep classification proposals eligible regardless of whether their legacy action predicts reuse correctly.
def test_case_b_actions_do_not_override_actual_path_resolution(taxonomy: TaxonomyDataset, action: str) -> None:
    taxonomy.categories.append(dict(id=30, name="Biology", service_id=2))
    taxonomy.specialities.append(dict(id=300, name="Zoology", category_id=30))
    row = {**load_rows("legacy_case_b.json")[0], "action": action}
    report = validate_outputs({"b": json.dumps([row])}, taxonomy, case_b_sources={"b": "skillsfuture"})
    assert report.records[0].status == "valid"
    assert report.records[0].target.speciality_id == 300
    assert report.records[0].raw["action"] == action
    assert not any(item.startswith("new_") for item in report.records[0].findings)


# Unresolved Case B outputs keep every placement field null despite the temporary service default.
def test_unresolved_case_b_output_keeps_null_service(taxonomy: TaxonomyDataset) -> None:
    row = load_rows("legacy_case_b.json")[0]
    row.update(category=None, speciality=None, confidence=None)
    report = validate_outputs({"b": json.dumps([row])}, taxonomy, case_b_sources={"b": "skillsfuture"})
    assert report.records[0].status == "unresolved"
    assert report.records[0].output.service is None
    assert report.records[0].raw == row


# Missing top-level services are taxonomy defects rather than permission to create arbitrary services.
def test_missing_industry_service_requires_review(taxonomy: TaxonomyDataset) -> None:
    taxonomy.services = [row for row in taxonomy.services if row["name"] != "Industry"]
    row = load_rows("legacy_case_a.json")[0]
    report = validate_outputs({"a": json.dumps([row])}, taxonomy, case_a_inputs={"a"})
    assert report.counts["review"] == 1
    assert any(item.startswith("missing_service:") for item in report.records[0].findings)
    assert report.records[0].output.service == "Industry"
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


@pytest.mark.parametrize("options", [
    {"case_a_inputs": {"missing"}}, {"case_a_inputs": {"a"}, "case_b_sources": {"a": "source"}},
    {"case_b_sources": {"a": " "}},
])
# Fail configuration mistakes before processing any records.
def test_invalid_adapter_configuration_is_rejected(taxonomy: TaxonomyDataset, options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        validate_outputs({"a": "[]"}, taxonomy, **options)


# Exercise SQL loading and intake together without a database or upstream matching result.
def test_sql_taxonomy_integration(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.sql"
    path.write_text(
        "INSERT INTO services (id,name) VALUES (1,'Industry');\n"
        "INSERT INTO categories (id,name,service_id) VALUES (10,'Data analytics',1);\n"
        "INSERT INTO specialities (id,name,category_id) VALUES (120,'Data presentation',10);\n",
        encoding="utf-8",
    )
    taxonomy = SqlDumpSource("sql-fixture", [path]).load()
    row = load_rows("legacy_case_a.json")[0]
    report = validate_outputs({"a": json.dumps([row])}, taxonomy, case_a_inputs={"a"})
    assert report.counts["valid"] == 1
    assert report.records[0].target.speciality_id == 120
    assert ClassificationOutput.model_validate_json(report.merged_outputs[0].model_dump_json())
