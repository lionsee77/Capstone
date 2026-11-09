"""Exercise advisory liveness and parent identity through real review decisions."""
from __future__ import annotations

import json

import pytest

from taxonomy_pipeline.enrichment.deduplication import deduplicate_outputs
from taxonomy_pipeline.enrichment.models import ResolutionReport, ReviewDecision
from taxonomy_pipeline.enrichment.preview import build_preview
from taxonomy_pipeline.enrichment.resolution import resolve_outputs
from taxonomy_pipeline.enrichment.review_queue import build_review_queue
from taxonomy_pipeline.enrichment.validation import validate_outputs
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


@pytest.fixture
# Keep parent identities explicit while isolating incoming names from baseline matches.
def baseline() -> TaxonomyDataset:
    return TaxonomyDataset(name="test", services=[
        {"id": 1, "name": "Industry"}, {"id": 2, "name": "Function"},
    ], categories=[], specialities=[], tags=[], speciality_tags=[])


# Supply source evidence using the producer contract and stable, distinct tag labels.
def row(identifier: str, category: str, speciality: str = "Navigation", service: str = "Industry") -> dict:
    return dict(source_dataset="feed", tag_id=identifier, tag=identifier, original_classification="classified",
                service=service, category=category, speciality=speciality, reason="Fixture", confidence=0.8)


# Resolve fixtures through intake and deduplication rather than constructing cached findings.
def resolve(rows: list[dict], baseline: TaxonomyDataset, decisions: list[ReviewDecision] = ()) -> ResolutionReport:
    evidence = deduplicate_outputs(validate_outputs({"input": json.dumps(rows)}, baseline))
    return resolve_outputs(evidence, baseline, decisions)


@pytest.mark.parametrize("action", ["exclude", "defer"])
@pytest.mark.parametrize("sibling", [False, True])
# A withdrawn speciality keeps its parent live only if another retained child still uses it.
def test_withdrawing_speciality_removes_its_branch_from_category_warnings(
    baseline: TaxonomyDataset, action: str, sibling: bool,
) -> None:
    rows = [row("Payroll", "Marine"), row("Diving", "Marine", "Scheduling", "Function")]
    if sibling:
        rows.append(row("Cooking", "Marine", "Catering"))
    initial = resolve(rows, baseline)
    proposal = next(item for item in initial.screening.proposals if item.level == "speciality"
                    and item.path[0] == "Industry" and item.path[-1] == "navigation")
    decision = ReviewDecision(proposal_id=proposal.proposal_id, action=action, reason="Withdraw branch")
    resolution = resolve(rows, baseline, [decision])
    assert len(resolution.effective_screening.repeated_names) == int(sibling)
    preview = build_preview(resolution, baseline)
    withdrawn = next(item for item in preview.rows if item.row_type == "placement" and item.tag == "Payroll")
    assert "warning.repeated_" not in withdrawn.note
    queue = build_review_queue(resolution, None, baseline)
    assert queue.repeated_names == resolution.effective_screening.repeated_names


# Approving one of a tag's paths clears the collision instead of treating the declined path as pending.
def test_partial_path_approval_clears_warning_from_csv_and_review_queue(baseline: TaxonomyDataset) -> None:
    rows = [row("Payroll", "Marine"), row("Payroll", "Retail")]
    initial = resolve(rows, baseline)
    assert len(initial.effective_screening.repeated_names) == 1
    decision = ReviewDecision(members=[{"source_dataset": "feed", "tag_id": "Payroll"}], action="create",
                              reason="Choose Marine", approved_paths=[{
                                  "service": "Industry", "category": "Marine", "speciality": "Navigation",
                              }])
    resolution = resolve(rows, baseline, [decision])
    preview = build_preview(resolution, baseline)
    assert resolution.effective_screening.repeated_names == []
    assert build_review_queue(resolution, None, baseline).repeated_names == []
    placements = [item for item in preview.rows if item.row_type == "placement"]
    assert {item.status for item in placements} == {"proposed", "unapproved"}
    assert all("warning.repeated_" not in item.note for item in placements)
    assert preview.counts.added_specialities == 1


# Incoming parent variants represent one unresolved parent question, not two confirmed child locations.
def test_held_parent_spelling_variants_do_not_create_a_child_collision(baseline: TaxonomyDataset) -> None:
    resolution = resolve([row("Payroll", "Marine"), row("Diving", "Marines")], baseline)
    assert all(item.blocks_creation for item in resolution.effective_screening.proposals)
    assert resolution.effective_screening.repeated_names == []
    assert not any(item.code.startswith("warning.repeated_")
                   for item in build_preview(resolution, baseline).validation.findings)


# Aliases share one labelled resolved parent while original paths remain visible as evidence.
def test_alias_cluster_lists_two_parent_groups_with_the_resolved_category(baseline: TaxonomyDataset) -> None:
    baseline.categories = [{"id": 10, "name": "Marine", "service_id": 1}]
    rows = [row("Payroll", "Marine"), row("Diving", "Seafaring"), row("Cooking", "Retail")]
    initial = resolve(rows, baseline)
    alias = next(item for item in initial.screening.proposals if item.name == "Seafaring")
    decision = ReviewDecision(proposal_id=alias.proposal_id, action="reuse", existing_concept_id=10,
                              reason="Same category")
    resolution = resolve(rows, baseline, [decision])
    [warning] = resolution.effective_screening.repeated_names
    assert "across 2 distinct parents" in warning.message
    assert warning.message.count("Parent ") == 2
    assert "Parent Industry > Marine (category ID 10)" in warning.message
    assert "Industry > Marine > Navigation" in warning.message
    assert "Industry > Seafaring > Navigation" in warning.message
    assert len(warning.proposal_ids) == 3
    assert build_review_queue(resolution, None, baseline).repeated_names == [warning]


# Advisory proposals reach the queue even when no screening hold or explicit review exists.
def test_queue_shares_persisted_findings_and_replays_legacy_reports(baseline: TaxonomyDataset) -> None:
    resolution = resolve([row("Payroll", "Marine"), row("Diving", "Retail")], baseline)
    saved = ResolutionReport.model_validate_json(resolution.model_dump_json())
    queue = build_review_queue(saved, None, baseline)
    assert len(queue.repeated_names) == 1
    assert {item.proposal_id for item in queue.proposals} == set(queue.repeated_names[0].proposal_ids)
    assert all(item.status == "new" and not item.blocks_creation for item in queue.proposals)
    assert {item.tag for item in queue.tags} == {"Payroll", "Diving"}
    assert all(item.status == "accepted" for item in queue.tags)
    legacy = saved.model_copy(update={"effective_screening": None})
    assert build_review_queue(legacy, None, baseline) == queue


# Placement notes stay constant-sized while a single check row carries a large cluster's evidence.
def test_large_clusters_have_compact_notes_and_stable_final_row_order(baseline: TaxonomyDataset) -> None:
    rows = [row(tag, f"Sector {index}", "Operations") for index in range(200)
            for tag in ("Payroll", "Diving", "Cooking")]
    resolution = resolve(rows, baseline)
    before = resolution.model_dump_json()
    preview = build_preview(resolution, baseline)
    placements = [item for item in preview.rows if item.row_type == "placement"]
    assert len(placements) == 600
    assert max(len(item.note) for item in placements) < 150
    assert all("warning.repeated_speciality_name" in item.note for item in placements)
    [check] = [item for item in preview.rows if item.status == "warning.repeated_speciality_name"]
    assert check.note.count("Parent ") == 200
    assert resolution.model_dump_json() == before
    assert preview.rows == build_preview(resolve(list(reversed(rows)), baseline), baseline).rows
