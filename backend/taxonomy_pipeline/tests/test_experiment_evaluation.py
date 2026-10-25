"""Test prompt-evaluation coverage, validation summaries and action mismatches."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.evaluate_case_b import evaluate_output, run_classification_validator
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

FIXTURES = Path(__file__).parent / "fixtures" / "classification"


def test_evaluate_output_reports_missing_and_unexpected_case_b_tags(tmp_path: Path) -> None:
    output = tmp_path / "case_b.json"
    output.write_text(json.dumps([{"tag_id": "3001"}, {"tag_id": "9999"}]), encoding="utf-8")

    metrics = evaluate_output(output, {"3001", "3002"})

    assert metrics == {
        "expected_case_b_tags": 2,
        "output_tags": 2,
        "missing_case_b_tags": ["3002"],
        "unexpected_tags": ["9999"],
    }


def _row(tag_id: str, action: str, **changes: object) -> dict[str, object]:
    return {
        "action": action, "source_dataset": "skillsfuture", "tag_id": tag_id, "tag": f"Skill {tag_id}",
        "original_classification": "unclassified", "service": "Subject", "category": "Data analytics",
        "speciality": "Data presentation", "confidence": 0.8, "reason": "Proposed.",
    } | changes


@pytest.fixture
def baseline() -> TaxonomyDataset:
    return TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))


def test_validator_results_are_summarised(tmp_path: Path, baseline: TaxonomyDataset) -> None:
    proposals = tmp_path / "proposals.json"
    proposals.write_text(json.dumps([
        _row("3001", "assign_existing"),
        _row("3002", "assign_existing", speciality="Brand new speciality"),
        _row("3003", "propose_new_category", service="Others", category="New"),
        _row("3004", "assign_existing", confidence="high"),
        _row("3005", "assign_existing", needs_review=True),
    ]), encoding="utf-8")

    result = run_classification_validator(proposals, baseline)

    assert result["passed"] is False
    assert result["records"] == 5
    assert result["counts"]["valid"] == 3 and result["counts"]["review"] == 1 and result["counts"]["invalid"] == 1
    assert result["records_needing_review"] == 4  # the mismatch, the missing service, the invalid row, the flag
    assert result["findings_by_code"] == {"invalid_output": 1, "missing_service": 1, "new_speciality": 1}
    assert result["action_mismatches"] == {"assign_existing -> propose_new_speciality": 1}
    assert result["action_mismatch_count"] == 1


def test_an_output_whose_actions_match_the_baseline_passes(tmp_path: Path, baseline: TaxonomyDataset) -> None:
    proposals = tmp_path / "proposals.json"
    proposals.write_text(json.dumps([
        _row("3001", "assign_existing"),
        _row("3002", "propose_new_speciality", speciality="Brand new speciality"),
        _row("3003", "propose_new_category", category="Brand new category", speciality="Anything"),
    ]), encoding="utf-8")

    result = run_classification_validator(proposals, baseline)

    assert result["passed"] is True and result["action_mismatch_count"] == 0
    assert result["records_needing_review"] == 0


def test_an_unreadable_output_is_not_reported_as_zero_errors(tmp_path: Path, baseline: TaxonomyDataset) -> None:
    proposals = tmp_path / "proposals.json"
    proposals.write_text('{"not": "a list"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="not a readable JSON array"):
        run_classification_validator(proposals, baseline)
