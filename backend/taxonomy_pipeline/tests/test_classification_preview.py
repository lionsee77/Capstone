"""Check preview assembly independently of producer formats and database writes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from taxonomy_pipeline.enrichment.deduplication import deduplicate_outputs
from taxonomy_pipeline.enrichment.models import MergePreview, ResolutionReport, ReviewDecision, TaxonomyPath
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
        reason="Reviewed", **(dict(action="create", approved_paths=[TaxonomyPath(
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
