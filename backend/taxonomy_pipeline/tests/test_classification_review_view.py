"""Saved review reads must preserve evidence and never apply or log decisions again."""
from __future__ import annotations

from pathlib import Path

import pytest

from taxonomy_pipeline.enrichment.models import ResolutionReport
from taxonomy_pipeline.enrichment.review_view import ProposalNotFound, build_review_view, load_review_view


# Preserve cross-parent and cross-level matches, their scores, and the separate reviewer choice.
def test_review_preserves_all_matching_branches_and_final_decision(saved_proposal_run: Path) -> None:
    view = load_review_view(saved_proposal_run, level="speciality")
    assert view.semantic_status == "complete" and view.total == 1
    item = view.proposals[0]
    assert item.proposed_parent == ["Subject", "Computing"]
    assert item.proposed_path == ["Subject", "Computing", "Data visualizatio"]
    assert item.proposal.path == ["Subject", "computing", "data visualizatio"]
    assert item.proposal.suggested_action == "defer"
    assert item.decision.action == "reuse" and item.decision.existing_concept_id == 20
    assert item.resolved_node.id == 20 and item.resolved_node.name == "Data visualization"
    matches = item.proposal.matches
    assert len(matches) == 4
    assert {match.concept.level for match in matches} == {"category", "speciality"}
    assert {tuple(match.concept.path) for match in matches if match.concept.level == "speciality"} == {
        ("Subject", "Computing", "Data visualization"), ("Industry", "Computing", "Data visualization"),
    }
    assert {match.same_parent for match in matches} == {True, False}
    assert all(match.semantic_score == 0.8 and "semantic" in match.match_types for match in matches)
    assert all(0 <= match.lexical_score <= 1 for match in matches)


# Paging limits proposals only and never trims a proposal's candidate list.
def test_review_pagination_is_stable_and_keeps_unreviewed_suggestions(saved_proposal_run: Path) -> None:
    whole = load_review_view(saved_proposal_run)
    first = load_review_view(saved_proposal_run, limit=1)
    second = load_review_view(saved_proposal_run, offset=1, limit=1)
    assert first.total == second.total == 2
    assert first.proposals + second.proposals == whole.proposals
    assert first.proposals[0].decision is None
    assert first.proposals[0].proposal.suggested_action == "reuse"
    assert load_review_view(saved_proposal_run, offset=2).proposals == []
    detail = load_review_view(saved_proposal_run, proposal_id=second.proposals[0].proposal.proposal_id, limit=1)
    assert detail.proposals == second.proposals and detail.total == 1
    with pytest.raises(ProposalNotFound):
        load_review_view(saved_proposal_run, proposal_id="missing")


# Saved semantic failures and unavailable checks stay visible without retrying matching.
def test_review_retains_failed_checks_without_mutating_saved_state(saved_proposal_run: Path) -> None:
    resolution = ResolutionReport.model_validate_json(
        (saved_proposal_run / "resolution.json").read_text(encoding="utf-8"),
    )
    resolution.screening.semantic_status = "failed"
    resolution.screening.skipped_checks = {"semantic": "Model unavailable"}
    before = resolution.model_dump_json()
    view = build_review_view(resolution)
    assert view.semantic_status == "failed" and view.skipped_checks == {"semantic": "Model unavailable"}
    assert resolution.model_dump_json() == before


@pytest.mark.parametrize("problem", ["missing_screening", "wrong_baseline"])
# Incomplete or mismatched reports must not silently become an empty review queue.
def test_review_rejects_unusable_saved_screening(saved_proposal_run: Path, problem: str) -> None:
    resolution = ResolutionReport.model_validate_json(
        (saved_proposal_run / "resolution.json").read_text(encoding="utf-8"),
    )
    if problem == "missing_screening":
        resolution.screening = None
    else:
        resolution.screening.baseline_snapshot_id = "another-baseline"
    with pytest.raises(ValueError, match="rerun merge-preview"):
        build_review_view(resolution)


@pytest.mark.parametrize(("offset", "limit"), [(-1, 50), (0, 0), (0, 201)])
# Library and CLI callers receive the same bounded-pagination validation as HTTP callers.
def test_review_rejects_invalid_pagination(saved_proposal_run: Path, offset: int, limit: int) -> None:
    with pytest.raises(ValueError, match="offset"):
        load_review_view(saved_proposal_run, offset=offset, limit=limit)


@pytest.mark.parametrize("paging", [{"offset": 3}, {"level": "speciality"}])
# One proposal by ID can't also be filtered or paged: that could only return an empty page instead of it.
def test_review_rejects_a_proposal_id_combined_with_a_level_or_offset(saved_proposal_run: Path, paging) -> None:
    category = load_review_view(saved_proposal_run, level="category").proposals[0].proposal.proposal_id
    with pytest.raises(ValueError, match="don't combine it with a level or an offset"):
        load_review_view(saved_proposal_run, proposal_id=category, limit=1, **paging)
