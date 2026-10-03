"""Exercise the producer boundary without running classification or matching engines."""
from __future__ import annotations

import json
import re
from itertools import product
from pathlib import Path

import pytest
from pydantic import ValidationError

from taxonomy_pipeline.classification.models import (
    CLASSIFICATION_OUTPUTS,
    CanonicalMapping,
    ClassificationOutput,
    DuplicateCandidate,
    LegacyClassificationOutput,
    MergePreview,
    PlacementTarget,
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
        "source_dataset", "tag_id", "tag", "original_classification",
        "service", "category", "speciality", "confidence", "reason",
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


@pytest.mark.parametrize(
    "null_fields", [flags for flags in product((False, True), repeat=4) if any(flags) and not all(flags)],
)
# Require unresolved records to clear the whole placement instead of leaving a partial path.
def test_partial_placements_are_rejected(placement: dict[str, object], null_fields: tuple[bool, ...]) -> None:
    for field, clear in zip(("service", "category", "speciality", "confidence"), null_fields, strict=True):
        if clear:
            placement[field] = None
    with pytest.raises(ValidationError, match="all present or all null"):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize("service", ["Industry", "Function", "Subject", "Others"])
# Accept each contract service without attempting taxonomy lookup in the data model.
def test_supported_services_are_accepted(placement: dict[str, object], service: str) -> None:
    placement["service"] = service
    assert ClassificationOutput.model_validate(placement).service == service


@pytest.mark.parametrize("service", ["Unknown", "subject", " Subject ", 1, True, ["Subject"]])
# Require an exact service name instead of normalizing or coercing producer values.
def test_unsupported_services_are_rejected(placement: dict[str, object], service: object) -> None:
    placement["service"] = service
    with pytest.raises(ValidationError):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize("tag", [None, "", " \t\n", 42])
# Keep a meaningful source tag mandatory even when no placement was found.
def test_unresolved_records_still_require_tag_text(placement: dict[str, object], tag: object) -> None:
    placement.update(service=None, category=None, speciality=None, confidence=None, tag=tag)
    with pytest.raises(ValidationError):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize("field", ["source_dataset", "tag_id", "tag", "service", "category", "speciality", "reason"])
# Reject blank identifiers and explanations while preserving meaningful original text.
def test_blank_text_is_rejected(placement: dict[str, object], field: str) -> None:
    placement[field] = " \t\n"
    with pytest.raises(ValidationError):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize("field", [
    "source_dataset", "tag_id", "tag", "original_classification",
    "service", "category", "speciality", "confidence", "reason",
])
# Require every agreed field even when an unresolved record supplies explicit nulls.
def test_missing_fields_are_rejected(placement: dict[str, object], field: str) -> None:
    del placement[field]
    with pytest.raises(ValidationError):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize(("field", "value"), [
    ("tag_id", 42), ("original_classification", "B"), ("original_classification", True),
    ("category", ["Data analytics"]), ("tag", None), ("tag", 42),
])
# Keep required classification fields strict without coercing invalid values.
def test_wrong_types_are_rejected(
    placement: dict[str, object], field: str, value: object,
) -> None:
    placement[field] = value
    with pytest.raises(ValidationError):
        ClassificationOutput.model_validate(placement)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("value", ["upstream-42", {"scores": [0.8, None]}, None])
# Ignore extra input fields without storing or serializing them in either classification model.
def test_extra_fields_are_ignored(placement: dict[str, object], legacy: bool, value: object) -> None:
    if legacy:
        del placement["service"]
    model = LegacyClassificationOutput if legacy else ClassificationOutput
    record = model.model_validate({
        **placement, "metadata": value, "original_tag": "Ignored alias", "proposed_tag": "Ignored proposal",
    })
    assert record.model_dump() == placement
    assert json.loads(record.model_dump_json()) == placement
    assert not hasattr(record, "metadata")
    assert record.model_extra is None


# Preserve source text exactly so normalization remains an explicit matching step.
def test_tag_text_is_preserved(placement: dict[str, object]) -> None:
    placement["tag"] = "  DATA Visualization!  "
    assert ClassificationOutput.model_validate(placement).tag == "  DATA Visualization!  "


# Reject legacy payloads explicitly so compatibility handling stays outside the contract.
def test_previous_eight_field_payload_is_rejected(placement: dict[str, object]) -> None:
    placement["original_tag"] = placement.pop("tag")
    del placement["service"]
    with pytest.raises(ValidationError) as error:
        ClassificationOutput.model_validate(placement)
    assert {(item["loc"], item["type"]) for item in error.value.errors()} == {
        (("tag",), "missing"), (("service",), "missing"),
    }


@pytest.mark.parametrize("raw", ['[{"tag_id":', '{"tag_id": "42"}', "null"])
# Require a well-formed JSON array at the producer boundary.
def test_invalid_json_or_non_array_payloads_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        CLASSIFICATION_OUTPUTS.validate_json(raw)


# Ensure fixtures cover valid and invalid paths without implementing taxonomy validation here.
def test_fixtures_cover_multiple_placements_source_collisions_and_invalid_paths() -> None:
    baseline = TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))
    services = {row["id"]: row["name"] for row in baseline.services}
    categories = {row["id"]: (services[row["service_id"]], row["name"]) for row in baseline.categories}
    paths = {(*categories[row["category_id"]], row["name"]) for row in baseline.specialities}
    case_a = CLASSIFICATION_OUTPUTS.validate_json((FIXTURES / "case_a.json").read_text(encoding="utf-8"))
    case_b = CLASSIFICATION_OUTPUTS.validate_json((FIXTURES / "case_b.json").read_text(encoding="utf-8"))
    invalid = CLASSIFICATION_OUTPUTS.validate_json((FIXTURES / "invalid_placements.json").read_text(encoding="utf-8"))
    assert all((row.service, row.category, row.speciality) in paths for row in case_a + case_b)
    assert all((row.service, row.category, row.speciality) not in paths for row in invalid)
    assert any(("Subject", row.category, row.speciality) in paths and row.service == "Function" for row in invalid)
    assert case_a[0].tag_id == case_a[1].tag_id == case_b[0].tag_id
    assert case_a[0].speciality != case_a[1].speciality
    assert case_a[0].source_dataset != case_b[0].source_dataset
    assert case_b[2].tag == case_b[3].tag == "Python"
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


# Keep the published producer schema synchronized with the runtime Pydantic contract.
def test_published_json_schema_matches_the_model() -> None:
    path = Path(__file__).parents[1] / "schemas" / "classification_output.schema.json"
    published = json.loads(path.read_text(encoding="utf-8"))
    assert published == CLASSIFICATION_OUTPUTS.json_schema()


# Ensure the examples producers copy from the contract pass the consumer's validation.
def test_contract_json_examples_round_trip() -> None:
    path = Path(__file__).parents[1] / "CLASSIFICATION_OUTPUT_CONTRACT.md"
    examples = re.findall(r"```json\n(.*?)\n```", path.read_text(encoding="utf-8"), re.DOTALL)
    assert examples
    for example in examples:
        records = CLASSIFICATION_OUTPUTS.validate_json(example)
        assert json.loads(CLASSIFICATION_OUTPUTS.dump_json(records)) == json.loads(example)


@pytest.mark.parametrize("category_id,speciality_id", [(10, 120), (10, None), (None, None)])
# Allow existing paths and new child nodes without assigning premature database IDs.
def test_placement_target_supports_proposed_additions(category_id: int | None, speciality_id: int | None) -> None:
    target = PlacementTarget(service_id=1, category_id=category_id, speciality_id=speciality_id)
    assert PlacementTarget.model_validate_json(target.model_dump_json()) == target


# An existing speciality cannot be reused underneath a newly proposed category.
def test_existing_speciality_requires_existing_parent() -> None:
    with pytest.raises(ValidationError, match="existing speciality requires an existing category"):
        PlacementTarget(service_id=1, category_id=None, speciality_id=120)
