"""Test prompt-evaluation coverage and validator failure handling."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.evaluate_case_b import evaluate_output, run_classification_validator


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


def test_validator_results_are_summarised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    proposals = tmp_path / "proposals.json"
    proposals.write_text("[]", encoding="utf-8")

    def run(command: list[str], **_: object) -> SimpleNamespace:
        validated = Path(command[command.index("--output") + 1])
        validated.write_text(json.dumps([
            {"review_status": "needs_review", "validation_errors": ["bad path"], "validation_warnings": ["note"]},
            {"review_status": "pending", "validation_errors": ["bad path"], "validation_warnings": []},
        ]), encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="validator output", stderr="")

    monkeypatch.setattr("experiments.evaluate_case_b.subprocess.run", run)
    result = run_classification_validator(proposals, tmp_path / "skillsfuture.sql", tmp_path)

    assert result["passed"] is False
    assert result["records"] == 2
    assert result["records_needing_review"] == 1
    assert result["error_count"] == 2
    assert result["errors_by_message"] == {"bad path": 2}
    assert result["warning_count"] == 1
    assert result["warnings_by_message"] == {"note": 1}


def test_validator_failure_without_output_is_not_reported_as_zero_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    proposals = tmp_path / "proposals.json"
    proposals.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        "experiments.evaluate_case_b.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="failed", stderr="missing dependency"),
    )

    with pytest.raises(RuntimeError, match="did not produce validated output"):
        run_classification_validator(proposals, tmp_path / "skillsfuture.sql", tmp_path)
