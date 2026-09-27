"""Exercise the producer boundary without running classification or matching engines."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from taxonomy_pipeline.classification.models import (
    CLASSIFICATION_OUTPUTS,
    CanonicalMapping,
    ClassificationOutput,
    DuplicateCandidate,
    MergePreview,
    PreviewCounts,
    ReviewDecision,
    SourceTagRef,
    ValidationFinding,
    ValidationReport,
)
from taxonomy_pipeline.datasets.base import TaxonomyDataset

FIXTURES = Path(__file__).parent / "fixtures" / "classification"


@pytest.fixture
# Load a fresh producer record so tests cannot change another test's inputs.
def placement() -> dict[str, object]:
    return json.loads((FIXTURES / "case_b.json").read_text(encoding="utf-8"))[0]


@pytest.mark.parametrize("filename", ["case_a.json", "case_b.json", "unresolved.json", "invalid_placements.json"])
# Preserve every producer field when reading and writing the shared JSON handoff.
def test_json_fixtures_round_trip_without_extra_metadata(filename: str) -> None:
    raw = (FIXTURES / filename).read_text(encoding="utf-8")
    records = CLASSIFICATION_OUTPUTS.validate_json(raw)
    assert json.loads(CLASSIFICATION_OUTPUTS.dump_json(records)) == json.loads(raw)
    assert set(records[0].model_dump()) == {
        "source_dataset", "tag_id", "original_tag", "original_classification",
        "category", "speciality", "confidence", "reason",
    }


@pytest.mark.parametrize("confidence", [-0.1, 1.1, "0.9", True, float("nan"), float("inf")])
# Reject confidence values that would silently change meaning during coercion.
def test_invalid_confidence_is_rejected(placement: dict[str, object], confidence: object) -> None:
    placement["confidence"] = confidence
    with pytest.raises(ValidationError):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize("confidence", [0, 0.5, 1])
# Accept both boundary scores and ordinary numeric confidence values.
def test_confidence_accepts_zero_through_one(placement: dict[str, object], confidence: float) -> None:
    placement["confidence"] = confidence
    assert ClassificationOutput.model_validate(placement).confidence == confidence


@pytest.mark.parametrize("field", ["category", "speciality", "confidence"])
# Require unresolved records to clear the whole placement instead of leaving a partial path.
def test_partial_placements_are_rejected(placement: dict[str, object], field: str) -> None:
    placement[field] = None
    with pytest.raises(ValidationError, match="all present or all null"):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize("field", ["source_dataset", "tag_id", "original_tag", "category", "speciality", "reason"])
# Reject blank identifiers and explanations while preserving meaningful original text.
def test_blank_text_is_rejected(placement: dict[str, object], field: str) -> None:
    placement[field] = " \t\n"
    with pytest.raises(ValidationError):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize("field", [
    "source_dataset", "tag_id", "original_tag", "original_classification",
    "category", "speciality", "confidence", "reason",
])
# Require every agreed field even when an unresolved record supplies explicit nulls.
def test_missing_fields_are_rejected(placement: dict[str, object], field: str) -> None:
    del placement[field]
    with pytest.raises(ValidationError):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize(("field", "value"), [
    ("tag_id", 42), ("original_classification", "B"), ("original_classification", True),
    ("category", ["Data analytics"]), ("comparison_result_id", "upstream-42"),
])
# Keep the simple eight-field format strict instead of accepting legacy or coerced inputs.
def test_wrong_types_and_extra_metadata_are_rejected(
    placement: dict[str, object], field: str, value: object,
) -> None:
    placement[field] = value
    with pytest.raises(ValidationError):
        ClassificationOutput.model_validate(placement)


# Preserve source text exactly so normalization remains an explicit matching step.
def test_original_tag_text_is_preserved(placement: dict[str, object]) -> None:
    placement["original_tag"] = "  DATA Visualization!  "
    assert ClassificationOutput.model_validate(placement).original_tag == "  DATA Visualization!  "


@pytest.mark.parametrize("raw", ['[{"tag_id":', '{"tag_id": "42"}', "null"])
# Require a well-formed JSON array at the producer boundary.
def test_invalid_json_or_non_array_payloads_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        CLASSIFICATION_OUTPUTS.validate_json(raw)


# Ensure fixtures cover valid and invalid paths without implementing taxonomy validation here.
def test_fixtures_cover_multiple_placements_source_collisions_and_invalid_paths() -> None:
    baseline = TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))
    categories = {row["id"]: row["name"] for row in baseline.categories}
    paths = {(categories[row["category_id"]], row["name"]) for row in baseline.specialities}
    case_a = CLASSIFICATION_OUTPUTS.validate_json((FIXTURES / "case_a.json").read_text(encoding="utf-8"))
    case_b = CLASSIFICATION_OUTPUTS.validate_json((FIXTURES / "case_b.json").read_text(encoding="utf-8"))
    invalid = CLASSIFICATION_OUTPUTS.validate_json((FIXTURES / "invalid_placements.json").read_text(encoding="utf-8"))
    assert all((row.category, row.speciality) in paths for row in case_a + case_b)
    assert all((row.category, row.speciality) not in paths for row in invalid)
    assert case_a[0].tag_id == case_a[1].tag_id == case_b[0].tag_id
    assert case_a[0].speciality != case_a[1].speciality
    assert case_a[0].source_dataset != case_b[0].source_dataset
    assert case_b[2].original_tag == case_b[3].original_tag == "Python"
    assert case_b[2].category != case_b[3].category


@pytest.mark.parametrize(("action", "target", "links"), [
    ("reuse", None, []), ("create", 200, []), ("exclude", None, [120]), ("defer", None, [120]),
])
# Prevent contradictory review records before they reach the later merge builder.
def test_review_actions_require_consistent_targets_and_links(
    action: str, target: int | None, links: list[int],
) -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(
            members=[SourceTagRef(source_dataset="categorized-skills", tag_id="44")],
            action=action, existing_tag_id=target, approved_speciality_ids=links, reason="Reviewed",
        )


# Confirming equivalence must not implicitly approve links or mark validation complete.
def test_review_and_validation_defaults_do_not_imply_approval() -> None:
    decision = ReviewDecision(
        members=[SourceTagRef(source_dataset="plain-skills", tag_id="42")],
        action="create", reason="Same skill",
    )
    first, second = ValidationReport(), ValidationReport()
    first.findings.append(ValidationFinding(code="needs_review", message="Review the proposed path"))
    assert decision.approved_speciality_ids == []
    assert not first.complete
    assert second.findings == []


# Keep internal preview records serializable without adding fields to producer outputs.
def test_supporting_records_round_trip_as_a_preview() -> None:
    baseline = TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))
    source = SourceTagRef(source_dataset="categorized-skills", tag_id="42")
    other = SourceTagRef(source_dataset="plain-skills", tag_id="42")
    decision = ReviewDecision(members=[source, other], action="create", reason="Confirmed equivalent")
    unresolved = CLASSIFICATION_OUTPUTS.validate_json((FIXTURES / "unresolved.json").read_text(encoding="utf-8"))
    preview = MergePreview(
        taxonomy=baseline, baseline_snapshot_id=baseline.name,
        validation=ValidationReport(
            duplicate_candidates=[DuplicateCandidate(left=source, right=other, similarity=1, reason="Same name")],
            findings=[ValidationFinding(code="unresolved", message=unresolved[0].reason)],
            unresolved=unresolved,
        ),
        decisions=[decision],
        mappings=[CanonicalMapping(source=source, canonical_tag_id="new:visualization")],
        counts=PreviewCounts(omitted_records=1),
    )
    assert MergePreview.model_validate_json(preview.model_dump_json()) == preview
    assert preview.partial


# Keep the published producer schema synchronized with the actual Pydantic contract.
def test_published_json_schema_matches_the_model() -> None:
    path = Path(__file__).parents[1] / "schemas" / "classification_output.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == CLASSIFICATION_OUTPUTS.json_schema()
