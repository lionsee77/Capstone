"""Exercise hierarchy decisions through the existing tag resolution and preview flow."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from taxonomy_pipeline.classification.deduplication import deduplicate_outputs
from taxonomy_pipeline.classification.models import ResolutionReport, ReviewDecision, TaxonomyPath
from taxonomy_pipeline.classification.preview import build_preview
from taxonomy_pipeline.classification.resolution import resolve_outputs
from taxonomy_pipeline.classification.validation import validate_outputs
from taxonomy_pipeline.datasets.base import TaxonomyDataset


@pytest.fixture
# Keep identities independent of the production taxonomy and allow cross-branch merge checks.
def baseline() -> TaxonomyDataset:
    return TaxonomyDataset(
        name="review-baseline", services=[{"id": 1, "name": "Subject"}, {"id": 2, "name": "Industry"}],
        categories=[{"id": 10, "name": "Computing", "service_id": 1}],
        specialities=[{"id": 20, "name": "Data visualization", "category_id": 10}],
        tags=[{"id": 30, "name": "Existing skill"}], speciality_tags=[],
    )


# Build complete producer evidence for each test using the real validation and deduplication stages.
def screened(baseline: TaxonomyDataset, **changes: Any) -> ResolutionReport:
    row = dict(
        source_dataset="feed", tag_id="1", tag="Fresh skill", original_classification="classified",
        service="Subject", category="Computing", speciality="Data visualizatio", confidence=0.8,
        reason="Producer suggestion",
    ) | changes
    report = deduplicate_outputs(validate_outputs({"case.json": json.dumps([row])}, baseline))
    return resolve_outputs(report, baseline)


# Address a screened proposal with the same decision model used for tag groups.
def decision(result: ResolutionReport, level: str = "speciality", **changes: Any) -> ReviewDecision:
    proposal = next(item for item in result.screening.proposals if item.level == level)
    return ReviewDecision(**(dict(proposal_id=proposal.proposal_id, action="create", reason="Expert review") | changes))


# A near-match approval clears only the hierarchy hold and survives saved report replay.
def test_hierarchy_approval_resolves_a_near_match_and_replays(baseline: TaxonomyDataset) -> None:
    original = screened(baseline)
    assert original.counts["pending_sources"] == 1
    assert {item.level: item.suggested_action for item in original.screening.proposals} == {
        "category": "reuse", "speciality": "defer",
    }
    choice = decision(original)
    result = resolve_outputs(original.evidence, baseline, [choice], screening=original.screening)
    assert result.screening == original.screening  # Suggestions remain distinct from the final decision.
    assert result.decisions == [choice]
    preview = build_preview(ResolutionReport.model_validate_json(result.model_dump_json()), baseline)
    assert not preview.blocked and preview.counts.pending_sources == 0
    assert preview.counts.added_specialities == 1
    assert not next(row for row in preview.rows if row.row_type == "placement").needs_review


# Merging a hierarchy proposal selects an existing ID without adding or changing its canonical row.
def test_merge_reuses_an_existing_speciality_without_creating_a_row(baseline: TaxonomyDataset) -> None:
    original = screened(baseline)
    result = resolve_outputs(original.evidence, baseline, [decision(original, action="reuse", existing_concept_id=20)])
    preview = build_preview(result, baseline)
    assert preview.taxonomy.specialities == baseline.specialities
    assert preview.counts.added_specialities == 0 and preview.counts.reused_specialities == 1
    assert preview.taxonomy.speciality_tags[0]["speciality_id"] == 20
    assert "Resolved placement: Subject > Computing > Data visualization" in next(
        row.note for row in preview.rows if row.row_type == "placement"
    )


@pytest.mark.parametrize("level", ["category", "speciality"])
# Explicit creation cannot bypass a confirmed name or spelling equivalent, even under another branch.
def test_approval_of_an_exact_equivalent_is_rejected(baseline: TaxonomyDataset, level: str) -> None:
    original = screened(baseline, speciality="Data visualisation")
    with pytest.raises(ValueError, match="exact/equivalent"):
        resolve_outputs(original.evidence, baseline, [decision(original, level)])


@pytest.mark.parametrize("action", ["exclude", "defer"])
# A tag approval cannot bypass a rejected or deferred hierarchy proposal.
def test_hierarchy_rejection_and_deferral_hold_dependent_tag_placements(
    baseline: TaxonomyDataset, action: str,
) -> None:
    original = screened(baseline)
    tag = original.evidence.tags[0]
    placement = tag.placements[0]
    tag_choice = ReviewDecision(
        members=[tag.source], action="create", reason="Approve tag",
        approved_paths=[TaxonomyPath(service=placement.service, category=placement.category,
                                     speciality=placement.speciality)],
    )
    choice = decision(original, action=action)
    result = resolve_outputs(original.evidence, baseline, [tag_choice, choice])
    assert result.counts["pending_sources"] == 1 and not result.links
    assert f"Reviewer {action}" in result.dispositions[0].reason
    assert build_preview(result, baseline).taxonomy == baseline


# Selecting a category in another service preserves its real parent and rechecks child reuse there.
def test_category_merge_rechecks_children_and_preserves_existing_parent(baseline: TaxonomyDataset) -> None:
    original = screened(baseline, service="Industry")
    result = resolve_outputs(original.evidence, baseline, [
        decision(original, "category", action="reuse", existing_concept_id=10),
        decision(original, action="reuse", existing_concept_id=20),
    ])
    preview = build_preview(result, baseline)
    assert preview.taxonomy.categories == baseline.categories
    assert preview.taxonomy.specialities == baseline.specialities
    assert preview.counts.added_categories == preview.counts.added_specialities == 0
    assert {node.level: node.parent_id for node in result.hierarchy} == {"category": 1, "speciality": 10}
    assert preview.counts.pending_sources == 0


# A selected parent can make an exact child safely reusable without a separate child decision.
def test_parent_decision_unlocks_an_exact_child(baseline: TaxonomyDataset) -> None:
    original = screened(baseline, category="Computer science", speciality="Data visualization")
    result = resolve_outputs(original.evidence, baseline, [
        decision(original, "category", action="reuse", existing_concept_id=10),
    ])
    preview = build_preview(result, baseline)
    assert preview.counts.reused_specialities == 1 and preview.counts.pending_sources == 0


# Ambiguous exact matches become usable only after the reviewer selects one existing identity.
def test_reviewer_can_choose_one_ambiguous_spelling_equivalent(baseline: TaxonomyDataset) -> None:
    baseline.specialities.append({"id": 21, "name": "Data visualisation", "category_id": 10})
    original = screened(baseline, speciality="Data visualizations")
    proposal = next(item for item in original.screening.proposals if item.level == "speciality")
    assert proposal.blocks_creation
    result = resolve_outputs(original.evidence, baseline, [decision(original, action="reuse", existing_concept_id=21)])
    preview = build_preview(result, baseline)
    assert preview.counts.pending_sources == 0 and preview.counts.added_specialities == 0
    assert preview.taxonomy.speciality_tags[0]["speciality_id"] == 21


# A failed requested check must not be interpreted as permission to approve an unchecked new concept.
def test_hierarchy_approval_cannot_bypass_a_failed_semantic_check(baseline: TaxonomyDataset) -> None:
    original = screened(baseline, speciality="Astrophysics")
    proposal = next(item for item in original.screening.proposals if item.level == "speciality")
    assert proposal.suggested_action == "create"
    matcher = Mock()
    matcher.similarity.side_effect = RuntimeError("Unavailable")
    with pytest.raises(ValueError, match="semantic screening failed"):
        resolve_outputs(original.evidence, baseline, [decision(original)], semantic_matcher=matcher)


# New spelling clusters require an explicit disposition of the competing proposal before creation.
def test_rejecting_a_competing_variant_allows_one_new_category(baseline: TaxonomyDataset) -> None:
    template = screened(baseline).evidence.tags[0].placements[0].model_dump()
    rows = [dict(template, tag_id=str(index), tag=tag, category=category, speciality="Astrophysics")
            for index, (tag, category) in enumerate([
                ("Fire prevention", "Task organization"), ("Payroll management", "Task organisation"),
            ])]
    report = deduplicate_outputs(validate_outputs({"cases.json": json.dumps(rows)}, baseline))
    initial = resolve_outputs(report, baseline)
    proposals = [item for item in initial.screening.proposals if item.level == "category"]
    keep = ReviewDecision(proposal_id=proposals[0].proposal_id, action="create", reason="Retain this label")
    with pytest.raises(ValueError, match="share a match_key"):
        resolve_outputs(report, baseline, [keep])
    reject = ReviewDecision(proposal_id=proposals[1].proposal_id, action="exclude", reason="Duplicate proposal")
    result = resolve_outputs(report, baseline, [keep, reject])
    preview = build_preview(result, baseline)
    assert not preview.blocked and preview.counts.added_categories == 1
    assert preview.counts.pending_sources == 1  # Rejecting a placement does not discard its source tag.


@pytest.mark.parametrize("target", [999, 10])
# IDs must identify a valid concept at the proposal's own level, not merely any baseline row.
def test_merge_rejects_missing_and_wrong_level_targets(baseline: TaxonomyDataset, target: int) -> None:
    original = screened(baseline)
    with pytest.raises(ValueError, match="valid existing speciality"):
        resolve_outputs(original.evidence, baseline, [decision(original, action="reuse", existing_concept_id=target)])


# An explicit target under another category requires a consistent parent decision as well.
def test_speciality_merge_rejects_an_inconsistent_parent(baseline: TaxonomyDataset) -> None:
    baseline.categories.append({"id": 11, "name": "Engineering", "service_id": 2})
    baseline.specialities.append({"id": 21, "name": "Robotics", "category_id": 11})
    original = screened(baseline)
    with pytest.raises(ValueError, match="reuse its parent category first"):
        resolve_outputs(original.evidence, baseline, [decision(original, action="reuse", existing_concept_id=21)])


# Conflicting decisions and stale proposal identities must fail instead of silently losing a reviewer choice.
def test_unknown_and_conflicting_hierarchy_decisions_fail(baseline: TaxonomyDataset) -> None:
    original = screened(baseline)
    with pytest.raises(ValueError, match="Unknown hierarchy proposal"):
        resolve_outputs(original.evidence, baseline, [
            ReviewDecision(proposal_id="stale", action="defer", reason="Wait"),
        ])
    with pytest.raises(ValueError, match="Conflicting decisions"):
        resolve_outputs(original.evidence, baseline, [decision(original), decision(original, action="exclude")])


@pytest.mark.parametrize("changes", [
    {"members": [{"source_dataset": "feed", "tag_id": "1"}]},
    {"existing_tag_id": 30}, {"action": "reuse"}, {"existing_concept_id": 20}, {"reason": " "},
])
# One strict model distinguishes tag and hierarchy targets and requires a meaningful review reason.
def test_shared_decision_contract_rejects_mixed_or_incomplete_targets(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ReviewDecision.model_validate(dict(proposal_id="proposal", action="create", reason="Reviewed") | changes)
