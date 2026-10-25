"""Resolve synthetic concepts from arbitrary datasets without producer-format assumptions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from taxonomy_pipeline.enrichment.deduplication import deduplicate_outputs
from taxonomy_pipeline.enrichment.models import DeduplicationReport, PlacementPath, ResolutionReport, ReviewDecision
from taxonomy_pipeline.enrichment.resolution import resolve_outputs
from taxonomy_pipeline.enrichment.validation import validate_outputs
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

FIXTURES = Path(__file__).parent / "fixtures" / "classification"


@pytest.fixture
# Provide all four services with arbitrary IDs so no tier depends on Ankit's numeric assignments.
def baseline() -> TaxonomyDataset:
    return TaxonomyDataset(
        name="arbitrary-baseline",
        services=[{"id": index + 11, "name": name} for index, name in enumerate(
            ["Industry", "Function", "Subject", "Others"],
        )],
        categories=[{"id": 37, "name": "Computing", "service_id": 13}],
        specialities=[{"id": 93, "name": "Languages", "category_id": 37}],
        tags=[{"id": 501, "name": "SQL"}, {"id": 502, "name": "Database operations"}],
        speciality_tags=[{"id": 19, "speciality_id": 93, "tag_id": 501}],
    )


# Create contract records with controllable datasets, identities, labels, and hierarchy contexts.
def row(identifier: str = "x", label: str = "SQL", **changes: Any) -> dict[str, Any]:
    return dict(
        source_dataset="catalogue", tag_id=identifier, tag=label, original_classification="classified",
        service="Subject", category="Computing", speciality="Languages", confidence=0.8, reason="Proposal",
    ) | changes


# Exercise the real intake and deduplication boundary before resolution in every integration scenario.
def dedup(rows: list[dict[str, Any]], baseline: TaxonomyDataset) -> DeduplicationReport:
    return deduplicate_outputs(validate_outputs({"synthetic": json.dumps(rows)}, baseline))


# Make explicit group decisions with optional new paths, existing targets, and label choices.
def decision(
    members: list[tuple[str, str]] | None = None, **changes: Any,
) -> ReviewDecision:
    return ReviewDecision.model_validate(dict(
        members=[{"source_dataset": source, "tag_id": tag} for source, tag in (members or [("catalogue", "x")])],
        action="create", reason="Reviewed",
    ) | changes)


# Refer to a placement without relying on existing or temporary numeric IDs.
def path(**changes: str) -> PlacementPath:
    return PlacementPath(**(dict(service="Subject", category="Computing", speciality="Languages") | changes))


# Merge a reviewed group across unrelated source datasets and retain every approved parent link.
def test_confirmed_equivalents_merge_with_multiple_new_paths_and_source_evidence(baseline: TaxonomyDataset) -> None:
    report = dedup([
        row("x", "Database operations", category="Data", speciality="Operations"),
        row("x", "Managing databases", source_dataset="another feed", original_classification="unclassified",
            service="Function", category="Delivery", speciality="Operations"),
    ], baseline)
    reviewed = decision(
        [("catalogue", "x"), ("another feed", "x")], canonical_name="Database operations",
        approved_paths=[path(category="Data", speciality="Operations"),
                        path(service="Function", category="Delivery", speciality="Operations")],
    )
    before = report.model_dump_json(), baseline.model_dump_json(), reviewed.model_dump_json()
    result = resolve_outputs(report, baseline, [reviewed])
    assert len(result.tags) == 1 and len(result.mappings) == 2 and len(result.links) == 2
    assert result.tags[0].name == "Database operations"
    assert len(result.tags[0].members) == 2
    assert len(result.hierarchy) == 4
    categories = {node.id for node in result.hierarchy if node.level == "category"}
    assert all(node.parent_id in categories for node in result.hierarchy if node.level == "speciality")
    assert all(node.id.startswith(f"new:{node.level}:") for node in result.hierarchy)
    assert all(link.tag_id == result.tags[0].id for link in result.links)
    assert result.counts["added_links"] == 2
    assert all(not item.unapproved_paths for item in result.dispositions)
    assert before == (report.model_dump_json(), baseline.model_dump_json(), reviewed.model_dump_json())
    assert ResolutionReport.model_validate_json(result.model_dump_json()) == result
    assert result.evidence == report and result.partial  # Semantic checks were skipped upstream.


# Reuse baseline labels and IDs while deduplicating repeated approvals and existing link pairs.
def test_reuse_preserves_baseline_label_and_existing_link(baseline: TaxonomyDataset) -> None:
    report = dedup([row(label="Structured Query Language"), row(label="Structured Query Language")], baseline)
    reviewed = decision(action="reuse", existing_tag_id=501, approved_speciality_ids=[93, 93], approved_paths=[path()])
    result = resolve_outputs(report, baseline, [reviewed, reviewed])
    assert result.tags[0].id == 501 and result.tags[0].name == "SQL"
    assert result.links[0].existing and len(result.links) == 1
    assert result.counts["added_tags"] == result.counts["added_links"] == 0
    assert {node.id for node in result.hierarchy} == {37, 93}
    assert len(result.decisions) == 1 and not result.partial


# Add a link to an existing tag without recreating the tag or its existing parent category.
def test_existing_tag_can_gain_a_new_speciality_link(baseline: TaxonomyDataset) -> None:
    report = dedup([row(speciality="Reporting")], baseline)
    result = resolve_outputs(report, baseline, [decision(
        action="reuse", existing_tag_id=501, approved_paths=[path(speciality="Reporting")],
    )])
    assert result.counts["added_tags"] == 0 and result.counts["added_links"] == 1
    assert result.counts["added_category_nodes"] == 0 and result.counts["added_speciality_nodes"] == 1
    assert not result.links[0].existing


# Consolidate separate explicit reuse decisions targeting the same baseline tag and link.
def test_disjoint_groups_reusing_one_baseline_tag_share_one_canonical_record(baseline: TaxonomyDataset) -> None:
    report = dedup([row(), row("other", "Querying")], baseline)
    result = resolve_outputs(report, baseline, [
        decision(action="reuse", existing_tag_id=501, approved_speciality_ids=[93]),
        decision([("catalogue", "other")], action="reuse", existing_tag_id=501, approved_paths=[path()]),
    ])
    assert len(result.tags) == len(result.links) == 1
    assert len(result.tags[0].members) == len(result.mappings) == 2


# Do not merge homonyms when explicit singleton decisions say to retain separate concepts.
def test_same_name_concepts_remain_distinct_when_reviewed_separately(baseline: TaxonomyDataset) -> None:
    report = dedup([row("x", "Python"), row("y", "Python", speciality="Wildlife")], baseline)
    assert report.candidates
    result = resolve_outputs(report, baseline, [
        decision(approved_paths=[path()]),
        decision([("catalogue", "y")], approved_paths=[path(speciality="Wildlife")]),
    ])
    assert len(result.tags) == 2
    assert result.tags[0].id != result.tags[1].id


# A transitive chain of candidates supplies no approval to combine or insert any source concept.
def test_missing_decisions_stay_pending_without_creating_nodes(baseline: TaxonomyDataset) -> None:
    report = dedup([row("x", "Identical", category="New"), row("y", "Identical", category="New")], baseline)
    result = resolve_outputs(report, baseline)
    assert result.partial and result.counts["pending_sources"] == 2
    assert result.tags == result.links == result.hierarchy == result.mappings == []
    assert result.evidence.candidates == report.candidates


# Confirming concept identity does not silently approve all of its proposed placements.
def test_unapproved_paths_remain_visible_without_creating_unused_hierarchy(baseline: TaxonomyDataset) -> None:
    report = dedup([row(), row(category="Unapproved", speciality="Unused")], baseline)
    result = resolve_outputs(report, baseline, [decision(approved_paths=[path()])])
    assert result.partial and len(result.links) == 1
    assert {node.id for node in result.hierarchy} == {37, 93}
    assert result.dispositions[0].unapproved_paths == [path(category="Unapproved", speciality="Unused")]


# Keep deferred and excluded source records as explicit dispositions rather than dropping their evidence.
def test_excluded_and_deferred_sources_have_no_canonical_mappings(baseline: TaxonomyDataset) -> None:
    report = dedup([row(), row("y", "Other")], baseline)
    result = resolve_outputs(report, baseline, [
        decision(action="exclude"), decision([("catalogue", "y")], action="defer"),
    ])
    assert [item.status for item in result.dispositions] == ["excluded", "deferred"]
    assert result.tags == result.mappings == result.hierarchy == [] and result.partial


@pytest.mark.parametrize("service", ["Industry", "Function", "Subject", "Others"])
# Resolve proposed hierarchies below every contract service using the baseline's actual parent IDs.
def test_new_hierarchy_works_for_every_service(baseline: TaxonomyDataset, service: str) -> None:
    report = dedup([row(service=service, category="Novel", speciality="Novel")], baseline)
    result = resolve_outputs(report, baseline, [decision(
        approved_paths=[path(service=service, category="Novel", speciality="Novel")],
    )])
    category, speciality = result.hierarchy
    assert category.parent_id == next(item["id"] for item in baseline.services if item["name"] == service)
    assert speciality.parent_id == category.id and category.id != speciality.id


# Same names under different services and categories must receive distinct parent-scoped IDs.
def test_shared_labels_do_not_collapse_different_parent_paths(baseline: TaxonomyDataset) -> None:
    placements = [
        row(service="Industry", category="Shared", speciality="Shared"),
        row(service="Function", category="Shared", speciality="Shared"),
        row(service="Industry", category="Other", speciality="Shared"),
    ]
    report = dedup(placements, baseline)
    approvals = [
        PlacementPath(**{key: item[key] for key in ("service", "category", "speciality")}) for item in placements
    ]
    result = resolve_outputs(report, baseline, [decision(approved_paths=approvals)])
    assert len(result.hierarchy) == 6 and len({node.id for node in result.hierarchy}) == 6
    assert len(result.links) == 3


# Normalized repeated paths share nodes and links while their raw source labels remain available.
def test_label_variants_and_repeated_placements_share_one_link(baseline: TaxonomyDataset) -> None:
    report = dedup([row(category=" NEW ", speciality="Example"), row(category="new", speciality="example")], baseline)
    result = resolve_outputs(report, baseline, [decision(approved_paths=[path(category="new", speciality="EXAMPLE")])])
    assert len(result.hierarchy) == 2 and len(result.links) == 1
    assert len(result.evidence.tags[0].placements) == 2


# Stable IDs depend on identities and parent paths rather than report or decision ordering.
def test_resolved_content_is_stable_when_inputs_and_decisions_are_reordered(baseline: TaxonomyDataset) -> None:
    report = dedup([
        row("x", "First", category="New", speciality="Operations"),
        row("y", "Second", category="New", speciality="Operations"),
    ], baseline)
    decisions = [
        decision(approved_paths=[path(category="New", speciality="Operations")]),
        decision([("catalogue", "y")], approved_paths=[path(category="New", speciality="Operations")]),
    ]
    first = resolve_outputs(report, baseline, decisions)
    report.tags.reverse()
    report.hierarchy.reverse()
    baseline.services.reverse()
    baseline.tags.reverse()
    second = resolve_outputs(report, baseline, list(reversed(decisions)))
    assert first.model_dump(exclude={"evidence"}) == second.model_dump(exclude={"evidence"})
    expanded = dedup([
        row("z", "Unrelated"), row("x", "First", category="New", speciality="Operations"),
    ], baseline)
    later = resolve_outputs(expanded, baseline, decisions[:1])
    assert next(tag.id for tag in first.tags if tag.name == "First") == next(
        tag.id for tag in later.tags if tag.name == "First"
    )
    assert all(node in later.hierarchy for node in first.hierarchy)


@pytest.mark.parametrize("changes", [
    {"action": "reuse", "existing_tag_id": 999},
    {"approved_speciality_ids": [999]},
    {"approved_paths": [path(category="Not proposed")]},
    {"canonical_name": "Invented label"},
])
# Refuse nonexistent baseline targets and placements or labels not backed by the group's proposals.
def test_unbacked_decisions_are_rejected(baseline: TaxonomyDataset, changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        resolve_outputs(dedup([row()], baseline), baseline, [decision(**changes)])


# Choosing a label for a group with different source spellings must be explicit rather than order-based.
def test_different_source_labels_require_a_canonical_name(baseline: TaxonomyDataset) -> None:
    report = dedup([row(), row("y", "Structured Query Language")], baseline)
    with pytest.raises(ValueError, match="canonical_name"):
        resolve_outputs(report, baseline, [decision([("catalogue", "x"), ("catalogue", "y")])])


@pytest.mark.parametrize("target", [501, 502])
# A source cannot belong to competing decisions even when their reuse targets happen to agree.
def test_overlapping_decisions_block_consolidation(baseline: TaxonomyDataset, target: int) -> None:
    with pytest.raises(ValueError, match="overlapping"):
        resolve_outputs(dedup([row()], baseline), baseline, [
            decision(action="reuse", existing_tag_id=501, reason="First"),
            decision(action="reuse", existing_tag_id=target, reason="Second"),
        ])


# Review members must resolve by dataset and source ID, not by a tag name or bare numeric ID.
def test_unknown_source_identity_is_rejected(baseline: TaxonomyDataset) -> None:
    with pytest.raises(ValueError, match="unknown source"):
        resolve_outputs(dedup([row()], baseline), baseline, [decision([("wrong dataset", "x")])])


@pytest.mark.parametrize("change", ["renamed", "duplicate_tag_id", "duplicate_speciality", "new_path_now_exists"])
# Changed or ambiguous baseline targets require revalidation before IDs and links can be reused.
def test_changed_or_ambiguous_baseline_is_rejected(baseline: TaxonomyDataset, change: str) -> None:
    report = dedup([row()], baseline)
    if change == "renamed":
        baseline.specialities[0]["name"] = "Changed"
    elif change == "duplicate_tag_id":
        baseline.tags.append(dict(baseline.tags[0]))
    elif change == "duplicate_speciality":
        baseline.specialities.append(dict(baseline.specialities[0], id=94))
    else:
        report = dedup([row(speciality="New")], baseline)
        baseline.specialities.append({"id": 94, "name": "New", "category_id": 37})
    with pytest.raises(ValueError):
        resolve_outputs(report, baseline, [decision(action="reuse", existing_tag_id=501)])


# Missing upstream records and skipped matching checks remain visible despite accepted eligible tags.
def test_partial_upstream_evidence_is_carried_forward(baseline: TaxonomyDataset) -> None:
    intake = validate_outputs({
        "good": json.dumps([row(), row(service=None, category=None, speciality=None, confidence=None)]), "bad": "{",
    }, baseline)
    report = deduplicate_outputs(intake)
    result = resolve_outputs(report, baseline, [decision(approved_paths=[path()])])
    assert result.partial and result.counts["omitted_input_records"] == 1
    assert result.evidence.omitted_counts == report.omitted_counts
    assert not result.evidence.input_complete


# Empty input is valid and produces no accidental proposals or pending decisions.
def test_empty_report_resolves_to_an_empty_complete_result(baseline: TaxonomyDataset) -> None:
    result = resolve_outputs(dedup([], baseline), baseline)
    assert not result.partial and result.tags == result.hierarchy == result.links == []


@pytest.mark.parametrize("action", ["exclude", "defer", "reuse"])
# Internal review extensions retain strict action-specific constraints without changing producer JSON.
def test_invalid_review_extensions_are_rejected(action: str) -> None:
    with pytest.raises(ValidationError):
        decision(action=action, canonical_name="SQL", existing_tag_id=501 if action == "reuse" else None)
    if action != "reuse":
        with pytest.raises(ValidationError):
            decision(action=action, approved_paths=[path()])


# Apply the same screening to legacy and current outputs, including the legacy Industry default's conflicts.
def test_legacy_adapter_reports_use_the_same_resolution_entry_point() -> None:
    baseline = TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))
    intake = validate_outputs(
        {name: (FIXTURES / name).read_text(encoding="utf-8") for name in ("legacy_case_a.json", "legacy_case_b.json")},
        baseline, case_a_inputs={"legacy_case_a.json"}, case_b_sources={"legacy_case_b.json": "skillsfuture"},
        skip_case_a_handoffs=True,
    )
    report = deduplicate_outputs(intake)
    decisions = [ReviewDecision(
        members=[tag.source], action="create", reason="Reviewed separately", approved_paths=[
            PlacementPath(service=p.service, category=p.category, speciality=p.speciality) for p in tag.placements
        ],
    ) for tag in report.tags]
    result = resolve_outputs(report, baseline, decisions)
    assert [tag.name for tag in result.tags] == ["Quantum analysis"]
    assert result.counts["omitted_input_records"] == 1
    assert {item.source.tag_id: item.status for item in result.dispositions} == {
        "42": "pending", "48": "pending", "49": "accepted",
    }
    assert all(node.path[0] == "Industry" for node in result.hierarchy)
    assert {node.name for node in result.hierarchy} == {"Quantum science", "Quantum computing"}
    blocked_categories = [
        proposal for proposal in result.screening.proposals
        if proposal.level == "category" and proposal.blocks_creation
    ]
    assert {proposal.name for proposal in blocked_categories} == {"Data analytics", "Biology"}
    assert all(
        any(match.concept.path[0] == "Subject" and not match.same_parent
            and "normalized_name" in match.match_types for match in proposal.matches)
        for proposal in blocked_categories
    )
    current = deduplicate_outputs(validate_outputs({
        "current": json.dumps([output.model_dump() for output in intake.merged_outputs]),
    }, baseline))
    current_result = resolve_outputs(current, baseline, decisions)
    assert current_result.tags == result.tags
    assert current_result.hierarchy == result.hierarchy
    assert current_result.dispositions == result.dispositions


# Resolve the published current-contract fixture and review file through the full downstream sequence.
def test_shared_review_fixture_resolves_current_contract_sources() -> None:
    baseline = TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))
    report = deduplicate_outputs(validate_outputs({
        name: (FIXTURES / name).read_text(encoding="utf-8") for name in ("case_a.json", "case_b.json")
    }, baseline))
    decisions = [ReviewDecision.model_validate(item) for item in json.loads(
        (FIXTURES / "review_decisions.json").read_text(encoding="utf-8"),
    )]
    result = resolve_outputs(report, baseline, decisions)
    assert len(result.tags) == 4 and len(result.mappings) == 7 and len(result.links) == 5
    assert result.counts["pending_sources"] == 0
    assert sum(tag.name == "Python" for tag in result.tags) == 2
    assert next(tag for tag in result.tags if tag.name == "SQL").id == 200
    assert not any(item.unapproved_paths for item in result.dispositions)


@pytest.mark.parametrize("mutation", ["missing_hierarchy", "duplicate_source", "wrong_source", "wrong_variant"])
# A malformed handoff must not silently lose placement evidence or duplicate a source identity.
def test_inconsistent_deduplication_reports_are_rejected(baseline: TaxonomyDataset, mutation: str) -> None:
    report = dedup([row()], baseline)
    if mutation == "missing_hierarchy":
        report.hierarchy.pop()
    elif mutation == "duplicate_source":
        report.tags.append(report.tags[0].model_copy(deep=True))
    elif mutation == "wrong_source":
        report.tags[0].placements[0].tag_id = "different"
    else:
        report.hierarchy[0].variants[0][-1] = "different"
    with pytest.raises(ValueError):
        resolve_outputs(report, baseline)


# Mapping identifiers remain distinct even when source names and IDs contain common delimiters.
def test_structured_source_keys_avoid_delimiter_collisions(baseline: TaxonomyDataset) -> None:
    report = dedup([row("b:c", source_dataset="a"), row("c", source_dataset="a:b")], baseline)
    result = resolve_outputs(report, baseline, [
        decision([("a", "b:c")], approved_paths=[path()]),
        decision([("a:b", "c")], approved_paths=[path()]),
    ])
    assert len({tag.id for tag in result.tags}) == 2


# A result can be edited for presentation without changing any caller-owned evidence or decisions.
def test_result_evidence_and_automatically_accepted_sources_are_detached(baseline: TaxonomyDataset) -> None:
    report = dedup([row()], baseline)
    original = report.model_dump_json()
    result = resolve_outputs(report, baseline)
    result.evidence.tags[0].source.tag_id = "edited"
    result.dispositions[0].source.tag_id = "also edited"
    assert report.model_dump_json() == original


# Unflagged source tags keep all supplied placements, including new hierarchy nodes, without manual decisions.
def test_unflagged_tags_and_new_paths_are_accepted_automatically(baseline: TaxonomyDataset) -> None:
    report = dedup([
        row(label="Payroll", category="Novel", speciality="Reporting", confidence=0.0),
        row(label="Payroll", category="Novel", speciality="Operations"),
    ], baseline)
    result = resolve_outputs(report, baseline)
    assert result.counts["accepted_sources"] == 1 and result.counts["pending_sources"] == 0
    assert len(result.tags) == 1 and len(result.links) == 2
    assert result.counts["added_category_nodes"] == 1 and result.counts["added_speciality_nodes"] == 2
    assert "Automatically accepted" in result.dispositions[0].reason
    assert result.decisions == []  # Automatic policy is not a fabricated human review.
    assert not result.partial


# Only sources named in duplicate candidates are held when unrelated proposals share the same batch.
def test_candidates_hold_only_their_members_while_other_sources_proceed(baseline: TaxonomyDataset) -> None:
    report = dedup([
        row("a", "Python", category="Programming", speciality="Software development"),
        row("b", "Python", source_dataset="wildlife", category="Animals", speciality="Wildlife"),
        row("c", "Payroll", service="Function", category="Finance", speciality="Payroll administration"),
    ], baseline)
    result = resolve_outputs(report, baseline)
    assert result.counts["pending_sources"] == 2 and result.counts["accepted_sources"] == 1
    assert [tag.name for tag in result.tags] == ["Payroll"]
    assert all(node.path[0] == "Function" for node in result.hierarchy)
    assert all("Potential duplicate" in item.reason for item in result.dispositions if item.status == "pending")
    assert result.partial and result.evidence.skipped_checks


# A producer's review candidates are never accepted by default, even with no duplicate evidence at all.
def test_producer_flagged_placement_stays_pending_without_a_decision(baseline: TaxonomyDataset) -> None:
    report = dedup([
        row("a", "Fire Prevention", service="Industry", category="Aviation", speciality="Fire Prevention",
            confidence=0.3, needs_review=True),
        row("a", "Fire Prevention", service="Industry", category="Travel", speciality="Fire Prevention",
            confidence=0.2, needs_review=True),
        row("c", "Payroll", service="Function", category="Finance", speciality="Payroll administration"),
    ], baseline)
    result = resolve_outputs(report, baseline)
    pending = [item for item in result.dispositions if item.status == "pending"]
    assert [item.source.tag_id for item in pending] == ["a"]
    assert "Producer requested review: 2 candidate placement(s)" in pending[0].reason
    assert len(pending[0].unapproved_paths) == 2
    assert [tag.name for tag in result.tags] == ["Payroll"]  # the unflagged tag still proceeds
    assert result.partial


# A reviewer resolves a flagged tag by approving one of the candidate paths; the rest stay unapproved.
def test_reviewer_can_approve_one_of_several_candidate_paths(baseline: TaxonomyDataset) -> None:
    report = dedup([
        row(label="Fire Prevention", service="Industry", category="Aviation", speciality="Fire Prevention",
            needs_review=True),
        row(label="Fire Prevention", service="Industry", category="Travel", speciality="Fire Prevention",
            needs_review=True),
    ], baseline)
    chosen = path(service="Industry", category="Aviation", speciality="Fire Prevention")
    result = resolve_outputs(report, baseline, [decision(approved_paths=[chosen])])
    disposition = result.dispositions[0]
    assert disposition.status == "accepted"
    assert [item.category for item in disposition.unapproved_paths] == ["Travel"]
    assert [node.name for node in result.hierarchy] == ["Aviation", "Fire Prevention"]
    assert len(result.links) == 1


@pytest.mark.parametrize("action", ["exclude", "defer", "reuse", "create"])
# Explicit decisions override automatic placement acceptance, including deliberate omission of links.
def test_explicit_decisions_override_automatic_acceptance(baseline: TaxonomyDataset, action: str) -> None:
    result = resolve_outputs(dedup([row(category="New")], baseline), baseline, [decision(
        action=action, existing_tag_id=501 if action == "reuse" else None,
    )])
    assert not result.links and not result.hierarchy
    assert result.dispositions[0].status == {"exclude": "excluded", "defer": "deferred"}.get(action, "accepted")
    assert result.dispositions[0].reason == "Reviewed"


# Candidate references are validated before default acceptance so broken evidence cannot silently release tags.
def test_unknown_duplicate_candidate_source_is_rejected(baseline: TaxonomyDataset) -> None:
    report = dedup([row("a", "Python"), row("b", "Python")], baseline)
    report.candidates[0].right = report.candidates[0].right.model_copy(update={"tag_id": "unknown"})
    with pytest.raises(ValueError, match="Duplicate candidates"):
        resolve_outputs(report, baseline)
