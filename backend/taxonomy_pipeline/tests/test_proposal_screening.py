"""Verify hierarchy screening gates additions through the real merge and replay boundaries.

Small baselines and deterministic semantic doubles keep these checks independent
of producer exports, network access and downloaded sentence models.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from taxonomy_pipeline.enrichment.deduplication import deduplicate_outputs
from taxonomy_pipeline.enrichment.models import DeduplicationReport, ResolutionReport, ReviewDecision, TaxonomyPath
from taxonomy_pipeline.enrichment.preview import build_preview
from taxonomy_pipeline.enrichment.proposals import screening
from taxonomy_pipeline.enrichment.proposals.models import ScreenedProposal, ScreeningSettings
from taxonomy_pipeline.enrichment.resolution import resolve_outputs
from taxonomy_pipeline.enrichment.validation import validate_outputs
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


@pytest.fixture
# Supply unrelated baseline IDs so reuse is verified independently of production taxonomy numbering.
def baseline() -> TaxonomyDataset:
    return TaxonomyDataset(
        name="screening-baseline",
        services=[{"id": 11, "name": "Subject"}, {"id": 12, "name": "Industry"}],
        categories=[{"id": 37, "name": "Computing", "service_id": 11}],
        specialities=[{"id": 93, "name": "Data visualization", "category_id": 37}],
        tags=[{"id": 501, "name": "Existing skill"}],
        speciality_tags=[],
    )


# Exercise validation and hierarchy grouping before screening rather than constructing internal evidence.
def proposal_report(taxonomy: TaxonomyDataset, **changes: str) -> DeduplicationReport:
    row = dict(
        source_dataset="new-feed", tag_id="skill-1", tag="Fresh skill", original_classification="classified",
        service="Subject", category="Computing", speciality="Data visualization", confidence=0.8,
        reason="Producer placement",
    ) | changes
    return deduplicate_outputs(validate_outputs({"producer.json": json.dumps([row])}, taxonomy))


# Review the supplied source placement without inventing an alternative hierarchy or source identity.
def review(report: DeduplicationReport, action: str = "create") -> ReviewDecision:
    tag = report.tags[0]
    return ReviewDecision(
        members=[tag.source], action=action, reason="Reviewed concept and placement",
        existing_tag_id=501 if action == "reuse" else None,
        approved_paths=[TaxonomyPath(service=row.service, category=row.category, speciality=row.speciality)
                        for row in tag.placements] if action in {"create", "reuse"} else [],
    )


# Select one tier's evidence from a single-placement resolution report.
def screened(result: ResolutionReport, level: str = "speciality") -> ScreenedProposal:
    assert result.screening is not None
    return next(proposal for proposal in result.screening.proposals if proposal.level == level)


class SemanticScores:
    """Return one meaning-only candidate without invoking a sentence model."""

    # Track model use so replay tests can detect accidental rescoring.
    def __init__(self) -> None:
        self.calls = 0

    # Score one related pair and exact labels while leaving unrelated concepts below the threshold.
    def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray:
        self.calls += 1
        return np.array([
            [1.0 if query == choice else 0.86 if (query, choice) == ("decision support", "data visualization")
             else 0.0 for choice in choices] for query in queries
        ])


class FailedScores:
    """Exercise model exceptions and invalid matrix responses at the matcher boundary."""

    # Choose the failure mode without changing screening itself.
    def __init__(self, failure: str) -> None:
        self.failure = failure

    # Return malformed scores or raise the error a failed optional model would produce.
    def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray:
        if self.failure == "exception":
            raise RuntimeError("Model unavailable")
        if self.failure == "shape":
            return np.zeros((len(queries) + 1, len(choices)))
        values = {"nan": np.nan, "infinity": np.inf, "out_of_range": 1.5}
        return np.full((len(queries), len(choices)), values[self.failure])


@pytest.mark.parametrize("level", ["category", "speciality"])
@pytest.mark.parametrize(("existing", "incoming", "match_type"), [
    pytest.param("Computing", "COMPUTING", "normalized_name", id="case"),
    pytest.param("Data analysis", "  Data   analysis  ", "normalized_name", id="whitespace"),
    pytest.param("Computing", "Computing .", "normalized_name", id="trailing-punctuation"),
    pytest.param("Data-driven", "Data\u2013driven", "normalized_name", id="unicode-dash"),
    pytest.param("Research and Development", "Research & Development", "match_key", id="ampersand"),
    pytest.param("Data visualization", "Data visualisation", "match_key", id="spelling"),
    pytest.param("Database", "Databases", "match_key", id="plural"),
])
# Reuse the original node and its parent for each supported spelling rule at either hierarchy tier.
def test_same_parent_spelling_variants_reuse_existing_nodes(
    baseline: TaxonomyDataset, level: str, existing: str, incoming: str, match_type: str,
) -> None:
    rows = baseline.categories if level == "category" else baseline.specialities
    rows[0]["name"] = existing
    report = proposal_report(baseline, **{level: incoming})
    before = baseline.model_dump_json(), report.model_dump_json()
    result = resolve_outputs(report, baseline)
    proposal = screened(result, level)
    assert proposal.status == "reuse" and not proposal.blocks_creation
    assert proposal.existing_id == rows[0]["id"]
    candidate = next(match for match in proposal.matches if match.concept.id == rows[0]["id"])
    assert candidate.same_parent and match_type in candidate.match_types
    assert candidate.lexical_score == 1.0 and candidate.semantic_score is None
    assert candidate.concept.path[-1] == existing
    assert {node.id for node in result.hierarchy} == {37, 93}
    assert next(node for node in result.hierarchy if node.level == level).name == existing
    preview = build_preview(result, baseline)
    assert not preview.blocked and preview.counts.pending_sources == 0
    assert preview.counts.added_categories == preview.counts.added_specialities == 0
    assert preview.counts.added_tags == preview.counts.added_links == 1
    assert preview.taxonomy.speciality_tags[0]["speciality_id"] == 93
    assert before == (baseline.model_dump_json(), report.model_dump_json())


@pytest.mark.parametrize("level", ["category", "speciality"])
@pytest.mark.parametrize("action", ["create", "reuse"])
# Preserve every exact candidate and prevent tag approval from choosing an ambiguous hierarchy target.
def test_ambiguous_exact_variants_block_additions_even_with_a_tag_decision(
    baseline: TaxonomyDataset, level: str, action: str,
) -> None:
    rows = baseline.categories if level == "category" else baseline.specialities
    rows[0]["name"] = "Data visualization"
    rows.append(dict(rows[0], id=rows[0]["id"] + 1, name="Data visualisation"))
    report = proposal_report(baseline, **{level: "Data visualizations"})
    result = resolve_outputs(report, baseline, [review(report, action)])
    proposal = screened(result, level)
    assert proposal.status == "review" and proposal.blocks_creation and proposal.existing_id is None
    local = [match for match in proposal.matches if match.same_parent and "match_key" in match.match_types]
    assert {match.concept.id for match in local} == {row["id"] for row in rows}
    assert result.counts["pending_sources"] == 1
    assert result.tags == result.hierarchy == result.links == result.mappings == []
    preview = build_preview(result, baseline)
    assert preview.taxonomy == baseline and preview.counts.added_tags == 0
    assert preview.counts.pending_sources == 1


@pytest.mark.parametrize("level", ["category", "speciality"])
@pytest.mark.parametrize(("left", "right"), [
    ("Research & Development", "Research and Development"),
    ("Task organisation", "Task organization"),
    ("Database", "Databases"),
])
# Hold new variants from both producers, including children and explicit approvals, through saved preview replay.
def test_new_incoming_variants_hold_both_proposals_for_review(
    baseline: TaxonomyDataset, level: str, left: str, right: str,
) -> None:
    template = proposal_report(baseline).tags[0].placements[0].model_dump()
    if level == "speciality":
        baseline.categories[0]["name"] = "Sales and Marketing"
    rows = []
    for index, name in enumerate((left, right)):
        row = dict(template)
        row.update(
            source_dataset=f"case-{index}", tag_id=str(index),
            tag="Navigation skill" if index == 0 else "Payroll handling",
            original_classification="classified" if index == 0 else "unclassified",
            category="Sales & Marketing" if index == 0 else "Sales and Marketing",
            speciality="Orbital navigation" if index == 0 else "Payroll administration",
        )
        row[level] = name
        rows.append(row)
    inputs = {f"case-{index}.json": json.dumps([row]) for index, row in enumerate(rows)}
    report = deduplicate_outputs(validate_outputs(inputs, baseline))
    assert len(report.tags) == 2 and report.candidates == []
    result = resolve_outputs(report, baseline)
    cluster = [proposal for proposal in result.screening.proposals if proposal.level == level]
    assert len(cluster) == 2
    for proposal in cluster:
        assert proposal.status == "review" and proposal.blocks_creation and proposal.existing_id is None
        assert proposal.matches == []
        assert "share a match_key" in proposal.reason
        assert all(member.proposal_id in proposal.reason for member in cluster)
    if level == "category":
        assert all(proposal.blocks_creation for proposal in result.screening.proposals)
    else:
        assert all(proposal.existing_id == 37 for proposal in result.screening.proposals
                   if proposal.level == "category")
    decisions = [ReviewDecision(
        members=[tag.source], action="create", reason="Approve proposed placement",
        approved_paths=[TaxonomyPath(service=row.service, category=row.category, speciality=row.speciality)
                        for row in tag.placements],
    ) for tag in report.tags]
    approved = resolve_outputs(report, baseline, decisions, screening=result.screening)
    assert result.counts["pending_sources"] == approved.counts["pending_sources"] == 2
    assert approved.tags == approved.hierarchy == approved.links == []
    loaded = ResolutionReport.model_validate_json(approved.model_dump_json())
    assert build_preview(loaded, baseline).taxonomy == baseline
    reversed_report = deduplicate_outputs(validate_outputs(dict(reversed(list(inputs.items()))), baseline))
    assert resolve_outputs(reversed_report, baseline).screening == result.screening


# Identical match keys under genuinely different parents must not be treated as one incoming cluster.
def test_new_incoming_variants_under_different_parents_can_proceed(baseline: TaxonomyDataset) -> None:
    rows = []
    for index, (service, name) in enumerate([
        ("Subject", "Research & Development"), ("Industry", "Research and Development"),
    ]):
        row = proposal_report(baseline).tags[0].placements[0].model_dump()
        row.update(
            tag_id=str(index), tag="Navigation skill" if index == 0 else "Payroll handling",
            service=service, category=name,
            speciality="Orbital navigation" if index == 0 else "Payroll administration",
        )
        rows.append(row)
    report = deduplicate_outputs(validate_outputs({"producers.json": json.dumps(rows)}, baseline))
    result = resolve_outputs(report, baseline)
    assert all(proposal.status == "new" and not proposal.blocks_creation for proposal in result.screening.proposals)
    preview = build_preview(result, baseline)
    assert preview.counts.pending_sources == 0
    assert preview.counts.added_categories == preview.counts.added_specialities == preview.counts.added_tags == 2


# Complete placements omitted by intake for ambiguous exact names must still receive screening evidence.
def test_intake_held_ambiguous_paths_are_screened_without_becoming_eligible(baseline: TaxonomyDataset) -> None:
    baseline.specialities.append(dict(baseline.specialities[0], id=94))
    report = proposal_report(baseline)
    assert report.tags == [] and report.omitted_counts == {"review": 1}
    result = resolve_outputs(report, baseline)
    proposal = screened(result)
    assert proposal.blocks_creation and proposal.status == "review"
    assert {match.concept.id for match in proposal.matches} == {93, 94}
    assert proposal.source_tags == [("new-feed", "skill-1")]
    assert proposal.origins == [("producer.json", 0)]
    assert result.tags == result.hierarchy == result.links == []
    assert result.counts["omitted_input_records"] == 1


@pytest.mark.parametrize(("changes", "existing_level", "existing_id"), [
    ({"service": "Industry"}, "category", 37),
    ({"category": "Data visualization", "speciality": "Orbital navigation"}, "speciality", 93),
])
# An exact match across parents or levels cannot move an existing node or approve its proposed children.
def test_exact_matches_outside_the_parent_hold_the_branch(
    baseline: TaxonomyDataset, changes: dict[str, str], existing_level: str, existing_id: int,
) -> None:
    report = proposal_report(baseline, **changes)
    result = resolve_outputs(report, baseline, [review(report)])
    proposal = screened(result, "category")
    candidate = next(match for match in proposal.matches if match.concept.id == existing_id)
    assert candidate.concept.level == existing_level and not candidate.same_parent
    assert "normalized_name" in candidate.match_types
    assert proposal.blocks_creation and screened(result).blocks_creation
    assert result.counts["pending_sources"] == 1
    assert build_preview(result, baseline).taxonomy == baseline


@pytest.mark.parametrize("basis", ["lexical", "semantic"])
@pytest.mark.parametrize("action", ["create", "reuse", "exclude", "defer"])
# Near matches hold defaults but leave the reviewer free to approve, reuse a tag, exclude or defer its placement.
def test_near_matches_need_a_decision_without_confirming_equivalence(
    baseline: TaxonomyDataset, basis: str, action: str,
) -> None:
    speciality = "Data visualizatio" if basis == "lexical" else "Decision support"
    matcher = None if basis == "lexical" else SemanticScores()
    report = proposal_report(baseline, speciality=speciality)
    pending = resolve_outputs(report, baseline, semantic_matcher=matcher)
    proposal = screened(pending)
    candidate = next(match for match in proposal.matches if match.concept.id == 93)
    assert proposal.status == "review" and not proposal.blocks_creation and proposal.existing_id is None
    assert candidate.same_parent and candidate.match_types == [basis]
    if basis == "lexical":
        assert 0.9 <= candidate.lexical_score < 1 and candidate.semantic_score is None
    else:
        assert candidate.lexical_score < 0.9 and candidate.semantic_score == pytest.approx(0.86)
    assert pending.counts["pending_sources"] == 1 and pending.hierarchy == []
    result = resolve_outputs(report, baseline, [review(report, action)], screening=pending.screening)
    expected_status = "excluded" if action == "exclude" else "deferred" if action == "defer" else "accepted"
    assert result.dispositions[0].status == expected_status
    preview = build_preview(result, baseline)
    assert not preview.blocked
    if action in {"create", "reuse"}:
        assert preview.counts.added_specialities == preview.counts.added_links == 1
        assert preview.counts.added_categories == 0
        assert preview.counts.added_tags == (1 if action == "create" else 0)
        assert any(row["name"] == speciality and row["category_id"] == 37 for row in preview.taxonomy.specialities)
    else:
        assert preview.taxonomy == baseline and result.links == []


@pytest.mark.parametrize("failure", ["exception", "shape", "nan", "infinity", "out_of_range"])
# Failed requested meaning checks must not silently fall back to permitting a new hierarchy concept.
def test_failed_semantic_checks_hold_new_concepts_even_when_approved(
    baseline: TaxonomyDataset, failure: str,
) -> None:
    report = proposal_report(baseline, speciality="Orbital navigation")
    result = resolve_outputs(report, baseline, [review(report)], semantic_matcher=FailedScores(failure))
    assert result.screening.semantic_status == "failed"
    assert "Semantic screening failed" in result.screening.skipped_checks["semantic"]
    assert screened(result).blocks_creation and screened(result).status == "review"
    assert screened(result, "category").status == "reuse"
    assert result.partial and result.counts["pending_sources"] == 1
    assert result.tags == result.hierarchy == result.links == []
    preview = build_preview(result, baseline)
    assert preview.taxonomy == baseline and preview.counts.added_specialities == 0
    assert any(finding.code == "screening.semantic" for finding in preview.validation.findings)


# Not requesting semantic matching is distinct from a failed requested check and permits conflict-free additions.
def test_disabled_semantic_checks_allow_clear_lexical_additions(baseline: TaxonomyDataset) -> None:
    result = resolve_outputs(proposal_report(baseline, speciality="Orbital navigation"), baseline)
    assert result.screening.semantic_status == "disabled"
    assert screened(result).status == "new" and not screened(result).blocks_creation
    preview = build_preview(result, baseline)
    assert preview.counts.added_specialities == preview.counts.added_tags == 1
    assert preview.counts.pending_sources == 0


@pytest.mark.parametrize("approved", [False, True])
# Saved semantic candidates and custom thresholds must survive disk round trips without running a matcher again.
def test_saved_reports_replay_semantic_evidence_and_decisions_without_a_model(
    baseline: TaxonomyDataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approved: bool,
) -> None:
    report = proposal_report(baseline, speciality="Decision support")
    matcher = SemanticScores()
    settings = ScreeningSettings(lexical_threshold=0.99, semantic_threshold=0.8)
    result = resolve_outputs(
        report, baseline, [review(report)] if approved else [],
        semantic_matcher=matcher, screening_settings=settings,
    )
    saved = tmp_path / "resolution.json"
    saved.write_text(result.model_dump_json(), encoding="utf-8")
    loaded = ResolutionReport.model_validate_json(saved.read_text(encoding="utf-8"))

    # Fail the test if replay attempts even an optional semantic scoring call.
    def unexpected_scoring(*args: object, **kwargs: object) -> None:
        pytest.fail("Saved reports must replay without semantic scoring")

    monkeypatch.setattr(screening, "_semantic_scores", unexpected_scoring)
    replayed = resolve_outputs(loaded.evidence, baseline, loaded.decisions, screening=loaded.screening)
    assert replayed == loaded and matcher.calls == 1
    assert replayed.screening.settings == settings
    candidate = next(match for match in screened(replayed).matches if match.concept.id == 93)
    assert candidate.match_types == ["semantic"] and candidate.semantic_score == pytest.approx(0.86)
    preview = build_preview(loaded, baseline)
    assert not preview.blocked
    assert preview.counts.pending_sources == (0 if approved else 1)
    assert preview.counts.added_specialities == (1 if approved else 0)


# Reusing saved evidence against another baseline must fail before additions can be assembled.
def test_saved_screening_rejects_a_changed_baseline(baseline: TaxonomyDataset) -> None:
    result = resolve_outputs(proposal_report(baseline), baseline)
    baseline.specialities[0]["name"] = "Renamed capability"
    with pytest.raises(ValueError, match="original baseline"):
        resolve_outputs(result.evidence, baseline, screening=result.screening)
    with pytest.raises(ValueError, match="Baseline snapshot changed"):
        build_preview(result, baseline)


# A service label that identifies multiple baseline IDs must fail explicitly rather than imply a cross-parent match.
def test_duplicate_normalised_services_raise_a_clear_error(baseline: TaxonomyDataset) -> None:
    report = proposal_report(baseline)
    baseline.services.append({"id": 13, "name": " SUBJECT "})
    with pytest.raises(ValueError, match=r"Ambiguous baseline service name 'subject': IDs \[11, 13\]"):
        resolve_outputs(report, baseline)


# Indexed replay must retain duplicate malformed baseline rows even when their only match is semantic.
def test_semantic_replay_preserves_identical_baseline_candidates(baseline: TaxonomyDataset) -> None:
    baseline.specialities.append(dict(baseline.specialities[0]))
    report = proposal_report(baseline, speciality="Decision support")
    result = resolve_outputs(report, baseline, semantic_matcher=SemanticScores())
    assert len(screened(result).matches) == 2
    loaded = ResolutionReport.model_validate_json(result.model_dump_json())
    assert resolve_outputs(loaded.evidence, baseline, screening=loaded.screening) == loaded


@pytest.mark.parametrize("action", ["create", "reuse"])
# A blocked branch must not discard a safe approved placement from the same review decision.
def test_mixed_path_decisions_accept_safe_paths_and_flag_blocked_paths(
    baseline: TaxonomyDataset, action: str,
) -> None:
    safe = proposal_report(baseline).tags[0].placements[0].model_dump()
    blocked = dict(safe, service="Industry", speciality="Orbital navigation", needs_review=True)
    report = deduplicate_outputs(validate_outputs({"producer.json": json.dumps([safe, blocked])}, baseline))
    result = resolve_outputs(report, baseline, [review(report, action)])
    disposition = result.dispositions[0]
    assert disposition.status == "accepted"
    assert disposition.unapproved_paths == [TaxonomyPath(
        service="Industry", category="Computing", speciality="Orbital navigation",
    )]
    assert "Hierarchy screening blocked approved paths: Industry > computing > orbital navigation" in disposition.reason
    assert "Producer requested review" in disposition.reason
    assert len(result.links) == 1 and result.links[0].speciality_id == 93
    loaded = ResolutionReport.model_validate_json(result.model_dump_json())
    preview = build_preview(loaded, baseline)
    assert not preview.blocked and preview.counts.pending_sources == 0
    assert preview.counts.unapproved_placements == 1
    assert preview.counts.added_categories == preview.counts.added_specialities == 0
    assert preview.counts.added_tags == (1 if action == "create" else 0)
    assert {row.service: row.status for row in preview.rows if row.row_type == "placement"} == {
        "Subject": "proposed", "Industry": "unapproved",
    }


# Review output must retain all independent holds and the producer's original placement explanation.
def test_review_reasons_keep_producer_baseline_and_hierarchy_context(baseline: TaxonomyDataset) -> None:
    row = proposal_report(baseline).tags[0].placements[0].model_dump()
    row.update(tag="Existing skill", service="Industry", needs_review=True, reason="Pick a candidate branch")
    report = deduplicate_outputs(validate_outputs({"producer.json": json.dumps([row])}, baseline))
    preview = build_preview(resolve_outputs(report, baseline), baseline)
    placement = next(item for item in preview.rows if item.row_type == "placement")
    assert placement.status == "pending" and placement.reason == "Pick a candidate branch"
    for reason in ("Hierarchy screening:", "Potential duplicate:", "Producer requested review:"):
        assert reason in placement.review_reason


@pytest.mark.parametrize("mutation", ["missing_matches", "wrong_branch", "wrong_score", "false_reuse"])
# Replay must recompute exact and lexical evidence instead of trusting edited IDs, paths or classifications.
def test_saved_screening_rejects_inconsistent_evidence(baseline: TaxonomyDataset, mutation: str) -> None:
    result = resolve_outputs(proposal_report(baseline), baseline)
    loaded = ResolutionReport.model_validate_json(result.model_dump_json())
    proposal = screened(loaded)
    if mutation == "missing_matches":
        proposal.matches.clear()
    elif mutation == "wrong_branch":
        proposal.matches[0].concept.path[0] = "Industry"
    elif mutation == "wrong_score":
        proposal.matches[0].lexical_score = 0.1
    else:
        proposal.existing_id = 999
    with pytest.raises(ValueError, match="Screening evidence changed"):
        build_preview(loaded, baseline)
