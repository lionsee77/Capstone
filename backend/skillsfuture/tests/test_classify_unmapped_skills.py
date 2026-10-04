"""Tests for strict Case B input selection and proposal validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from classify_unmapped_skills import (
    case_b_tag_ids,
    proposal_is_well_formed,
    report_tag_ids,
)


def write_report(path: Path) -> None:
    report = {
        "results": [
            {
                "metric_id": "saturation",
                "details": {
                    "by_concept_level": {
                        "tags": {
                            "ambiguous_concepts": [
                                {
                                    "recommended_action": "ADD_CANDIDATE",
                                    "parent_status": "NO_PARENT",
                                    "incoming_ids": ["100", "100"],
                                },
                                {
                                    "recommended_action": "ADD_CANDIDATE",
                                    "parent_status": "HAS_PARENT",
                                    "incoming_ids": ["101"],
                                },
                                {
                                    "recommended_action": "SKIP_DUPLICATE",
                                    "parent_status": "NO_PARENT",
                                    "incoming_ids": ["102"],
                                },
                                {
                                    "recommended_action": "REVIEW_AMBIGUOUS",
                                    "parent_status": "NO_PARENT",
                                    "incoming_ids": ["103"],
                                },
                            ]
                        }
                    }
                },
            }
        ]
    }
    path.write_text(json.dumps(report), encoding="utf-8")


def test_report_tag_ids_selects_only_requested_case_b_records(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    write_report(report_path)

    assert report_tag_ids(report_path, {"ADD_CANDIDATE"}, "NO_PARENT") == ["100"]


def test_case_b_tag_ids_requires_report_and_known_tags(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    write_report(report_path)

    assert case_b_tag_ids(report_path, {"100": "A skill"}) == ["100"]

    with pytest.raises(FileNotFoundError, match="Case B report not found"):
        case_b_tag_ids(tmp_path / "missing.json", {"100": "A skill"})


def test_case_b_tag_ids_fails_when_report_has_no_matching_tags(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    write_report(report_path)

    with pytest.raises(ValueError, match="No Case B tags found"):
        case_b_tag_ids(report_path, {"999": "A skill"})


def test_proposal_is_well_formed_rejects_invalid_actions_and_confidence() -> None:
    proposal = {
        "tag": "Skill",
        "tag_id": "100",
        "action": "assign_existing",
        "category": "Technology",
        "speciality": "Software",
        "confidence": 0.8,
        "reason": "Matches the existing speciality.",
    }

    assert proposal_is_well_formed(proposal)
    assert not proposal_is_well_formed({**proposal, "action": "unknown"})
    assert not proposal_is_well_formed({**proposal, "confidence": 80})
