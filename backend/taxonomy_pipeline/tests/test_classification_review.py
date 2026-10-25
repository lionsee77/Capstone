"""Exercise hierarchy decisions through the existing tag resolution and preview flow."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from taxonomy_pipeline.enrichment.deduplication import deduplicate_outputs
from taxonomy_pipeline.enrichment.models import PlacementPath, ResolutionReport, ReviewDecision
from taxonomy_pipeline.enrichment.preview import build_preview
from taxonomy_pipeline.enrichment.resolution import resolve_outputs
from taxonomy_pipeline.enrichment.review import apply_hierarchy_decisions
from taxonomy_pipeline.enrichment.validation import validate_outputs
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


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


# A case-only difference from the baseline spelling isn't a move, so it gets no "Resolved placement" note.
def test_a_case_only_difference_is_not_noted_as_a_resolved_placement(baseline: TaxonomyDataset) -> None:
    original = screened(baseline, category="computing", speciality="Data visualizatio")
    result = resolve_outputs(original.evidence, baseline, [decision(original, action="reuse", existing_concept_id=20)])
    note = next(row.note for row in build_preview(result, baseline).rows if row.row_type == "placement")

    assert "Hierarchy decisions" in note and "Resolved placement: Subject > Computing > Data visualization" in note
    result = resolve_outputs(screened(baseline, category="computing", speciality="data visualization").evidence,
                             baseline)
    note = next(row.note for row in build_preview(result, baseline).rows if row.row_type == "placement")
    assert "Resolved placement" not in note


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
        approved_paths=[PlacementPath(service=placement.service, category=placement.category,
                                     speciality=placement.speciality)],
    )
    choice = decision(original, action=action)
    result = resolve_outputs(original.evidence, baseline, [tag_choice, choice])
    assert result.counts["pending_sources"] == 1 and not result.links
    assert f"Reviewer {action}" in result.dispositions[0].reason
    assert build_preview(result, baseline).taxonomy == baseline


# A category reuse must stay in the proposal's service, or every tag under it would move service silently.
def test_category_reuse_is_rejected_when_the_target_is_in_another_service(baseline: TaxonomyDataset) -> None:
    original = screened(baseline, service="Industry")
    with pytest.raises(ValueError, match="belongs to a different service than 'Industry'"):
        resolve_outputs(original.evidence, baseline, [
            decision(original, "category", action="reuse", existing_concept_id=10),
        ])


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


def _variant_categories(baseline: TaxonomyDataset) -> tuple[Any, list[Any]]:
    template = screened(baseline).evidence.tags[0].placements[0].model_dump()
    rows = [dict(template, tag_id=str(index), tag=tag, category=category, speciality="Astrophysics")
            for index, (tag, category) in enumerate([
                ("Fire prevention", "Task organization"), ("Payroll management", "Task organisation"),
            ])]
    report = deduplicate_outputs(validate_outputs({"cases.json": json.dumps(rows)}, baseline))
    proposals = [item for item in resolve_outputs(report, baseline).screening.proposals if item.level == "category"]
    return report, proposals


# Reusing one incoming spelling variant resolves the cluster, so its partner is no longer held for it.
def test_reusing_one_variant_releases_its_partner(baseline: TaxonomyDataset) -> None:
    report, proposals = _variant_categories(baseline)
    reuse = ReviewDecision(proposal_id=proposals[0].proposal_id, action="reuse", existing_concept_id=10,
                           reason="Same as Computing")

    undecided = resolve_outputs(report, baseline, [reuse])
    partner = next(item for item in undecided.screening.proposals if item.proposal_id == proposals[1].proposal_id)
    assert partner.blocks_creation  # The saved screening is unchanged evidence...
    effective = apply_hierarchy_decisions(undecided.screening, baseline, undecided.decisions)
    released = next(item for item in effective.proposals if item.proposal_id == proposals[1].proposal_id)
    assert not released.blocks_creation and "share a match_key" not in released.reason  # ...the hold is gone.

    create = ReviewDecision(proposal_id=proposals[1].proposal_id, action="create", reason="Distinct label")
    preview = build_preview(resolve_outputs(report, baseline, [reuse, create]), baseline)
    assert preview.counts.added_categories == 1 and preview.counts.pending_sources == 0


# A same-named speciality under another category doesn't stop create, matching integrate's duplicate policy.
def test_a_speciality_named_like_one_in_another_category_can_be_created(baseline: TaxonomyDataset) -> None:
    original = screened(baseline, category="Design", speciality="Data visualization")
    proposal = next(item for item in original.screening.proposals if item.level == "speciality")
    assert proposal.blocks_creation and not any(match.same_parent for match in proposal.matches)

    preview = build_preview(resolve_outputs(original.evidence, baseline, [decision(original)]), baseline)

    assert preview.counts.added_specialities == 1 and preview.counts.pending_sources == 0


# The same allowance doesn't extend to categories: an exact category name in another service still blocks.
def test_a_category_named_like_one_in_another_service_still_cannot_be_created(baseline: TaxonomyDataset) -> None:
    original = screened(baseline, service="Industry")
    with pytest.raises(ValueError, match="exact/equivalent"):
        resolve_outputs(original.evidence, baseline, [decision(original, "category")])


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
