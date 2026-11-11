"""Check preview assembly independently of producer formats and database writes."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from taxonomy_pipeline.enrichment.deduplication import deduplicate_outputs
from taxonomy_pipeline.enrichment.export import export_preview_csv
from taxonomy_pipeline.enrichment.models import MergePreview, PlacementPath, ResolutionReport, ReviewDecision
from taxonomy_pipeline.enrichment.preview import build_preview
from taxonomy_pipeline.enrichment.resolution import resolve_outputs
from taxonomy_pipeline.enrichment.validation import validate_outputs
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

FIXTURES = Path(__file__).parent / "fixtures" / "classification"


@pytest.fixture
# Return a fresh baseline with known hierarchy and one existing tag/link.
def baseline() -> TaxonomyDataset:
    return TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))


# Supply arbitrary source proposals through the current contract rather than handcrafted resolution internals.
def placement(**changes: Any) -> dict[str, Any]:
    return dict(
        source_dataset="independent-feed", tag_id="77", tag="Reporting", original_classification="classified",
        service="Subject", category="Data analytics", speciality="Data presentation", confidence=0.8, reason="Proposal",
    ) | changes


# Build explicit review decisions from chosen placements without approving other source records.
def approve(rows: list[dict[str, Any]], **changes: Any) -> ReviewDecision:
    members = {(row["source_dataset"], row["tag_id"]) for row in rows}
    return ReviewDecision(
        members=[{"source_dataset": source, "tag_id": identifier} for source, identifier in sorted(members)],
        reason="Reviewed", **(dict(action="create", approved_paths=[PlacementPath(
            service=row["service"], category=row["category"], speciality=row["speciality"],
        ) for row in rows]) | changes),
    )


# Run the real downstream stages so preview tests exercise valid serialized boundaries.
def resolve(
    rows: list[dict[str, Any]], baseline: TaxonomyDataset, decisions: list[ReviewDecision],
) -> ResolutionReport:
    report = deduplicate_outputs(validate_outputs({"input": json.dumps(rows)}, baseline))
    return resolve_outputs(report, baseline, decisions)


# Assemble reviewed fixture groups while preserving the baseline and each source mapping.
def test_builds_reviewed_combined_taxonomy_and_source_rows(baseline: TaxonomyDataset) -> None:
    report = deduplicate_outputs(validate_outputs({
        name: (FIXTURES / name).read_text(encoding="utf-8") for name in ("case_a.json", "case_b.json")
    }, baseline))
    decisions = [ReviewDecision.model_validate(raw) for raw in json.loads(
        (FIXTURES / "review_decisions.json").read_text(encoding="utf-8"),
    )]
    resolution = resolve_outputs(report, baseline, decisions)
    before = baseline.model_dump_json(), resolution.model_dump_json()
    preview = build_preview(resolution, baseline)
    assert not preview.blocked and preview.validation.complete
    assert preview.counts.added_tags == 3 and preview.counts.reused_tags == 1
    assert preview.counts.added_links == 5
    assert len(preview.taxonomy.tags) == 4 and len(preview.taxonomy.speciality_tags) == 6
    assert len(preview.mappings) == 7
    assert len([row for row in preview.rows if row.row_type == "placement"]) == 8
    assert preview.baseline_integrity.status == preview.preview_integrity.status == "ok"
    assert preview.partial  # Optional semantic matching did not run upstream.
    assert (baseline.model_dump_json(), resolution.model_dump_json()) == before
    assert MergePreview.model_validate_json(preview.model_dump_json()) == preview


# Proposed nodes and links preserve all four tiers and have valid temporary endpoints.
def test_adds_new_hierarchy_in_parent_order(baseline: TaxonomyDataset) -> None:
    row = placement(service="Industry", category="Marine", speciality="Operations")
    preview = build_preview(resolve([row], baseline, [approve([row])]), baseline)
    assert not preview.partial and not preview.blocked
    assert preview.counts.added_categories == preview.counts.added_specialities == preview.counts.added_tags == 1
    category = next(item for item in preview.taxonomy.categories if item["name"] == "Marine")
    speciality = next(item for item in preview.taxonomy.specialities if item["name"] == "Operations")
    assert category["service_id"] == 2 and speciality["category_id"] == category["id"]
    assert any(link["speciality_id"] == speciality["id"] for link in preview.taxonomy.speciality_tags)
    exported = next(item for item in preview.rows if item.row_type == "placement")
    assert (exported.category_change, exported.speciality_change, exported.tag_change, exported.link_change) == (
        "new", "new", "new", "new",
    )


# Existing links are shown as reuse without adding rows or assigning replacement IDs.
def test_repeated_existing_link_is_not_recreated(baseline: TaxonomyDataset) -> None:
    row = placement(tag="SQL", speciality="Programming")
    review = approve([row], action="reuse", existing_tag_id=200)
    preview = build_preview(resolve([row, row], baseline, [review, review]), baseline)
    assert preview.counts.added_tags == preview.counts.added_links == 0
    assert preview.counts.reused_tags == preview.counts.reused_links == 1
    assert len(preview.taxonomy.speciality_tags) == len(baseline.speciality_tags)
    assert next(row for row in preview.rows if row.row_type == "placement").status == "reused"


# Unapproved new tags and paths remain review rows instead of orphan additions in the combined taxonomy.
def test_accepted_tag_without_approved_links_is_omitted(baseline: TaxonomyDataset) -> None:
    row = placement(category="Unapproved", speciality="Unapproved")
    preview = build_preview(resolve([row], baseline, [approve([row], approved_paths=[])]), baseline)
    assert preview.partial and not preview.blocked
    assert preview.counts.added_tags == preview.counts.added_categories == 0
    assert preview.counts.unapproved_placements == 1 and not preview.mappings
    assert next(row for row in preview.rows if row.row_type == "placement").status == "unapproved"
    assert len(preview.validation.unresolved) == 1


# Partial review exposes pending, excluded, deferred, and unapproved placements separately.
def test_review_dispositions_and_unapproved_placements_are_visible(baseline: TaxonomyDataset) -> None:
    rows = [placement(tag_id=str(index)) for index in range(4)] + [placement(tag_id="0", speciality="Other")]
    decisions = [
        approve([rows[0]]), approve([rows[1]], action="exclude", approved_paths=[]),
        approve([rows[2]], action="defer", approved_paths=[]),
    ]
    preview = build_preview(resolve(rows, baseline, decisions), baseline)
    statuses = [row.status for row in preview.rows if row.row_type == "placement"]
    assert sorted(statuses) == ["deferred", "excluded", "pending", "proposed", "unapproved"]
    assert preview.counts.pending_sources == preview.counts.deferred_sources == preview.counts.excluded_sources == 1
    assert preview.counts.added_links == 1 and preview.counts.unapproved_placements == 1
    assert preview.partial


# Existing defects are reported without treating them as new errors caused by unrelated valid additions.
def test_baseline_defects_are_separated_from_new_defects(baseline: TaxonomyDataset) -> None:
    baseline.tags.extend([{"id": 999, "name": "Unused"}, {"id": 999, "name": "Duplicate ID"}])
    baseline.categories.append({"id": 999, "name": "Orphan", "service_id": 999})
    baseline.speciality_tags.append(dict(baseline.speciality_tags[0], id=2))
    row = placement()
    preview = build_preview(resolve([row], baseline, [approve([row])]), baseline)
    codes = {finding.code for finding in preview.validation.findings}
    assert {"baseline.duplicate_id", "baseline.invalid_endpoint", "baseline.duplicate_link"} <= codes
    assert not any(code.startswith("introduced.") for code in codes)
    assert not preview.blocked and preview.counts.added_tags == 1


# ID collisions introduced by new proposals block the whole batch even when the integrity metric says ok.
def test_new_structural_errors_block_additions(baseline: TaxonomyDataset) -> None:
    row = placement()
    resolution = resolve([row], baseline, [approve([row])])
    baseline.tags.append({"id": resolution.tags[0].id, "name": "Preexisting ID collision"})
    resolution = resolve([row], baseline, [approve([row])])
    preview = build_preview(resolution, baseline)
    assert preview.blocked and preview.partial and not preview.validation.complete
    assert any(finding.code == "introduced.duplicate_id" for finding in preview.validation.findings)
    assert preview.counts.added_tags == preview.counts.added_links == 0
    assert len(preview.taxonomy.tags) == len(baseline.tags) and not preview.mappings
    assert any(row.status == "blocked" for row in preview.rows)
    assert preview.preview_integrity.status == "ok"


@pytest.mark.parametrize("field", ["links", "hierarchy", "mappings", "tags", "dispositions"])
# Hand-edited resolution artifacts cannot bypass recorded decisions and create unapproved changes.
def test_modified_resolution_artifacts_block_the_preview(baseline: TaxonomyDataset, field: str) -> None:
    row = placement()
    resolution = resolve([row], baseline, [approve([row])])
    getattr(resolution, field).clear()
    preview = build_preview(resolution, baseline)
    assert preview.blocked and preview.counts.added_links == 0
    assert any(finding.code == "introduced.resolution_mismatch" for finding in preview.validation.findings)


# Snapshot identity includes baseline content rather than just its dataset name.
def test_changed_baseline_requires_new_resolution(baseline: TaxonomyDataset) -> None:
    row = placement()
    resolution = resolve([row], baseline, [approve([row])])
    baseline.tags[0]["status"] = "changed"
    with pytest.raises(ValueError, match="Baseline snapshot changed"):
        build_preview(resolution, baseline)


# Source record omissions remain counted independently of CSV rows and unique tag counts.
def test_upstream_omissions_are_reported_without_inventing_missing_source_rows(baseline: TaxonomyDataset) -> None:
    row = placement()
    report = deduplicate_outputs(validate_outputs({"input": json.dumps([
        row, placement(tag_id="unresolved", service=None, category=None, speciality=None, confidence=None), {},
    ])}, baseline))
    preview = build_preview(resolve_outputs(report, baseline, [approve([row])]), baseline)
    assert preview.counts.omitted_records == 2 and preview.partial
    omissions = {item.status: item.count for item in preview.rows if item.row_type == "omission"}
    assert omissions == {"invalid": 1, "unresolved": 1}
    assert len([item for item in preview.rows if item.row_type == "placement"]) == 1


# Reordering baseline rows and resolution collections produces the same review CSV content and snapshot.
def test_preview_rows_and_combined_taxonomy_are_deterministic(baseline: TaxonomyDataset) -> None:
    row = placement()
    resolution = resolve([row], baseline, [approve([row])])
    first = build_preview(resolution, baseline)
    baseline.categories.reverse()
    baseline.services.reverse()
    resolution.hierarchy.reverse()
    resolution.evidence.hierarchy.reverse()
    second = build_preview(resolution, baseline)
    assert first.rows == second.rows and first.taxonomy == second.taxonomy and first.counts == second.counts


# Metrics that cannot run are explicit supporting findings, not silent success or a loss of valid rows.
def test_integrity_failure_marks_partial_and_preserves_explicit_checks(baseline: TaxonomyDataset) -> None:
    baseline.tags.append({"id": 998, "name": {"unexpected": "value"}})
    row = placement()
    preview = build_preview(resolve([row], baseline, [approve([row])]), baseline)
    assert preview.partial and not preview.blocked and preview.counts.added_tags == 1
    assert preview.baseline_integrity.status == "error"
    assert any(item.code.startswith("supporting.") for item in preview.validation.findings)


# Fresh runs contain no additions or invented unresolved records when the producer arrays are empty.
def test_empty_inputs_produce_summary_rows_and_unchanged_taxonomy(baseline: TaxonomyDataset) -> None:
    preview = build_preview(resolve([], baseline, []), baseline)
    assert not preview.partial and not preview.blocked
    assert preview.counts.added_tags == 0 and not preview.validation.unresolved
    assert all(row.row_type == "summary" for row in preview.rows)


# Edited cached completeness flags cannot hide pending review or missing semantic checks.
def test_partial_status_is_recomputed_from_evidence(baseline: TaxonomyDataset) -> None:
    resolution = resolve([placement(tag_id="one"), placement(tag_id="two")], baseline, [])
    resolution.partial = False
    resolution.counts["pending_sources"] = 0
    preview = build_preview(resolution, baseline)
    assert preview.partial and preview.counts.pending_sources == 2


# Default acceptance reaches the preview as proposed additions while actual duplicate candidates stay pending.
def test_default_acceptance_adds_unflagged_paths_and_retains_candidate_review(baseline: TaxonomyDataset) -> None:
    rows = [
        placement(tag="Payroll", service="Industry", category="New sector", speciality="New practice"),
        placement(tag="Python", tag_id="one"), placement(tag="Python", tag_id="two"),
    ]
    resolution = resolve(rows, baseline, [])
    preview = build_preview(resolution, baseline)
    assert not preview.blocked
    assert preview.counts.added_categories == preview.counts.added_specialities == preview.counts.added_tags == 1
    assert preview.counts.pending_sources == 2 and preview.counts.added_links == 1
    statuses = {row.tag_id: row.status for row in preview.rows if row.row_type == "placement"}
    assert statuses == {"77": "proposed", "one": "pending", "two": "pending"}
    assert preview.decisions == []


# Treat equal names as separate approved concepts when reviews explicitly retained both identities.
def test_distinct_same_name_concepts_do_not_become_structural_errors(baseline: TaxonomyDataset) -> None:
    rows = [
        placement(tag_id="one", tag="Python"),
        placement(tag_id="two", tag="Python", category="Biology", speciality="Zoology"),
    ]
    preview = build_preview(resolve(rows, baseline, [approve([row]) for row in rows]), baseline)
    assert not preview.blocked and preview.counts.added_tags == 2
    assert preview.counts.pending_sources == 0
    assert preview.counts.added_categories == preview.counts.added_specialities == 0
    assert preview.preview_integrity.details["exact_duplicates"]["tags"]


# Tier positions are significant even though collection ordering is not significant during artifact checks.
def test_swapping_hierarchy_path_tiers_blocks_the_preview(baseline: TaxonomyDataset) -> None:
    row = placement()
    resolution = resolve([row], baseline, [approve([row])])
    resolution.hierarchy[0].path.reverse()
    preview = build_preview(resolution, baseline)
    assert preview.blocked


# Shared canonical tags do not approve a source placement that its own decision left unapproved.
def test_shared_reuse_target_does_not_hide_unapproved_source_placement(baseline: TaxonomyDataset) -> None:
    first, second = placement(tag_id="one"), placement(tag_id="two")
    decisions = [
        approve([first], action="reuse", existing_tag_id=200),
        approve([second], action="reuse", existing_tag_id=200, approved_paths=[]),
    ]
    preview = build_preview(resolve([first, second], baseline, decisions), baseline)
    statuses = {row.tag_id: row.status for row in preview.rows if row.row_type == "placement"}
    assert statuses == {"one": "proposed", "two": "unapproved"}
    assert preview.counts.added_links == 1 and preview.counts.unapproved_placements == 1


# One path proposed both confidently and as a review candidate keeps the review flag visible on its row.
def test_shared_path_shows_needs_review_when_any_variant_is_flagged(baseline: TaxonomyDataset) -> None:
    # The unflagged variant sorts first, so it is the representative the row is built from.
    rows = [placement(confidence=0.4), placement(confidence=0.9, needs_review=True)]
    preview = build_preview(resolve(rows, baseline, []), baseline)
    [row] = [row for row in preview.rows if row.row_type == "placement"]
    assert row.status == "pending"
    assert row.needs_review is True


@pytest.fixture
# Isolate incoming hierarchy collisions from baseline matches and existing tag review rules.
def empty_baseline(baseline: TaxonomyDataset) -> TaxonomyDataset:
    return baseline.model_copy(update={"categories": [], "specialities": [], "tags": [], "speciality_tags": []})


@pytest.mark.parametrize("level", ["category", "speciality"])
# Export one advisory cluster with both paths while leaving automatically accepted placements unchanged.
def test_surfaces_repeated_new_names_across_parents(
    empty_baseline: TaxonomyDataset, tmp_path: Path, level: str,
) -> None:
    rows = [
        placement(tag_id="one", tag="Payroll", service="Industry", category="Marine", speciality="Navigation"),
        placement(tag_id="two", tag="Diving", service="Function", category="Retail", speciality="Scheduling"),
    ]
    rows[1][level] = "  MARINE! " if level == "category" else "  NAVIGATION! "
    resolution = resolve(rows, empty_baseline, [])
    before = resolution.model_dump_json(), empty_baseline.model_dump_json()
    preview = build_preview(resolution, empty_baseline)
    warnings = [finding for finding in preview.validation.findings if finding.code.startswith("warning.repeated_")]
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.code == f"warning.repeated_{level}_name"
    assert "across 2 distinct parents" in warning.message
    for proposal in resolution.screening.proposals:
        if proposal.level == level:
            assert proposal.proposal_id in warning.message
            assert " > ".join(min(proposal.variants)) in warning.message
    assert not preview.blocked and preview.validation.complete
    assert preview.partial == resolution.partial
    assert preview.counts.added_categories == preview.counts.added_specialities == 2
    assert preview.counts.added_tags == preview.counts.added_links == 2
    assert preview.counts.pending_sources == 0 and preview.decisions == []
    assert (resolution.model_dump_json(), empty_baseline.model_dump_json()) == before
    path = export_preview_csv(preview, tmp_path / "preview.csv")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        exported = list(csv.DictReader(stream))
    placements = [row for row in exported if row["row_type"] == "placement"]
    assert len(placements) == 2
    assert all(row["status"] == "proposed" and row["needs_review"] == "False" for row in placements)
    assert all(warning.code in row["note"] and warning.message not in row["note"] for row in placements)
    assert [row["note"] for row in exported if row["status"] == warning.code] == [warning.message]
    reversed_preview = build_preview(resolve(list(reversed(rows)), empty_baseline, []), empty_baseline)
    assert preview.rows == reversed_preview.rows


@pytest.mark.parametrize("pending", [False, True])
# Report a repeated speciality under two categories even when their tags are still awaiting review.
def test_repeated_specialities_within_one_service_remain_visible(
    empty_baseline: TaxonomyDataset, pending: bool,
) -> None:
    rows = [
        placement(tag_id="one", tag="Payroll", category="Marine", speciality="Operations", needs_review=pending),
        placement(tag_id="two", tag="Diving", category="Retail", speciality="Operations", needs_review=pending),
    ]
    preview = build_preview(resolve(rows, empty_baseline, []), empty_baseline)
    warnings = [row for row in preview.rows if row.status == "warning.repeated_speciality_name"]
    assert len(warnings) == 1
    placements = [row for row in preview.rows if row.row_type == "placement"]
    assert all("warning.repeated_speciality_name" in row.note for row in placements)
    assert all(row.status == ("pending" if pending else "proposed") for row in placements)
    assert all(row.needs_review == pending for row in placements)


@pytest.mark.parametrize("second_path", [
    {"category": " MARINE! ", "speciality": " NAVIGATION! "},
    {"category": "Navigation", "speciality": "Scheduling"},
    {"category": "Retail", "speciality": "Navigations"},
])
# Count hierarchy nodes rather than source references and keep levels and non-normalized matches separate.
def test_does_not_warn_for_shared_paths_cross_level_names_or_spelling_matches(
    empty_baseline: TaxonomyDataset, second_path: dict[str, str],
) -> None:
    rows = [
        placement(tag_id="one", tag="Payroll", category="Marine", speciality="Navigation"),
        placement(tag_id="two", tag="Diving", **second_path),
    ]
    preview = build_preview(resolve(rows, empty_baseline, []), empty_baseline)
    assert not any(finding.code.startswith("warning.repeated_") for finding in preview.validation.findings)


# Existing same-named hierarchy nodes are baseline screening's responsibility, not new-proposal clusters.
def test_reused_baseline_names_do_not_count_as_new_repeated_names(empty_baseline: TaxonomyDataset) -> None:
    empty_baseline.categories = [
        {"id": 10, "name": "Marine", "service_id": 1}, {"id": 20, "name": "Marine", "service_id": 2},
    ]
    empty_baseline.specialities = [
        {"id": 100, "name": "Operations", "category_id": 10},
        {"id": 200, "name": "Operations", "category_id": 20},
    ]
    rows = [
        placement(tag_id="one", tag="Payroll", category="Marine", speciality="Operations"),
        placement(tag_id="two", tag="Diving", service="Industry", category="Marine", speciality="Operations"),
    ]
    preview = build_preview(resolve(rows, empty_baseline, []), empty_baseline)
    assert preview.counts.reused_categories == preview.counts.reused_specialities == 2
    assert not any(finding.code.startswith("warning.repeated_") for finding in preview.validation.findings)


@pytest.mark.parametrize("action", ["exclude", "defer"])
@pytest.mark.parametrize("hierarchy", [False, True])
# Withdrawing a competing tag or parent proposal clears the advisory without reviving its children.
def test_withdrawn_proposals_do_not_leave_repeated_name_warnings(
    empty_baseline: TaxonomyDataset, action: str, hierarchy: bool,
) -> None:
    rows = [
        placement(tag_id="one", tag="Payroll", category="Marine", speciality="Navigation"),
        placement(tag_id="two", tag="Diving", service="Industry", category="Marine", speciality="Navigation"),
    ]
    if hierarchy:
        initial = resolve(rows, empty_baseline, [])
        proposal = next(item for item in initial.screening.proposals
                        if item.level == "category" and item.path[0] == "Industry")
        decisions = [ReviewDecision(proposal_id=proposal.proposal_id, action=action, reason="Withdraw this branch")]
    else:
        decisions = [approve([rows[1]], action=action, approved_paths=[])]
    preview = build_preview(resolve(rows, empty_baseline, decisions), empty_baseline)
    assert not any(finding.code.startswith("warning.repeated_") for finding in preview.validation.findings)


# Reviewed aliases of one existing category still introduce only one same-named speciality.
def test_reviewed_parent_aliases_do_not_create_a_false_repeated_name_cluster(empty_baseline: TaxonomyDataset) -> None:
    empty_baseline.categories = [{"id": 10, "name": "Marine", "service_id": 1}]
    rows = [
        placement(tag_id="one", tag="Payroll", category="Marine", speciality="Navigation"),
        placement(tag_id="two", tag="Diving", category="Seafaring", speciality="Navigation"),
    ]
    initial = resolve(rows, empty_baseline, [])
    proposal = next(item for item in initial.screening.proposals
                    if item.level == "category" and item.name == "Seafaring")
    decisions = [ReviewDecision(
        proposal_id=proposal.proposal_id, action="reuse", existing_concept_id=10, reason="Same category",
    )]
    preview = build_preview(resolve(rows, empty_baseline, decisions), empty_baseline)
    assert preview.counts.added_specialities == 1 and preview.counts.added_links == 2
    assert not any(finding.code.startswith("warning.repeated_") for finding in preview.validation.findings)


# Approving both contextual placements preserves the warning as evidence without undoing either decision.
def test_repeated_name_warning_survives_explicit_approval(empty_baseline: TaxonomyDataset) -> None:
    rows = [
        placement(tag_id="one", tag="Payroll", category="Marine", speciality="Navigation", needs_review=True),
        placement(tag_id="two", tag="Diving", category="Retail", speciality="Navigation", needs_review=True),
    ]
    decisions = [approve([row]) for row in rows]
    resolution = resolve(rows, empty_baseline, decisions)
    preview = build_preview(resolution, empty_baseline)
    assert preview.decisions == resolution.decisions
    assert preview.counts.pending_sources == 0 and preview.counts.added_specialities == 2
    assert not preview.blocked and preview.validation.complete
    assert all("warning.repeated_speciality_name" in row.note and row.status == "proposed"
               for row in preview.rows if row.row_type == "placement")
