"""Joining a merge run's evidence: what a reviewer sees for a held tag or proposal."""
from __future__ import annotations

from taxonomy_pipeline.enrichment.models import (
    ClassificationOutput,
    DeduplicationReport,
    DuplicateCandidate,
    ResolutionReport,
    SourceDisposition,
    SourceTagRef,
    TagProposal,
)
from taxonomy_pipeline.enrichment.review_queue import build_review_queue
from taxonomy_pipeline.enrichment.routing import SaturationReport
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


def _ref(tag_id: str) -> SourceTagRef:
    return SourceTagRef(source_dataset="demo", tag_id=tag_id)


def _placement(tag_id: str, category: str, confidence: float) -> ClassificationOutput:
    return ClassificationOutput(
        source_dataset="demo", tag_id=tag_id, tag=f"tag {tag_id}", original_classification="classified",
        service="Function", category=category, speciality="Speciality", confidence=confidence, reason="Placed",
    )


def _resolution(dispositions: list[SourceDisposition], candidates: list[DuplicateCandidate] = ()) -> ResolutionReport:
    tags = [
        TagProposal(source=_ref(tag_id), tag=f"tag {tag_id}", original_classification="classified", origins=[],
                    placements=[_placement(tag_id, "Lower", 0.4), _placement(tag_id, "Higher", 0.9)])
        for tag_id in ("1", "2")
    ]
    evidence = DeduplicationReport(
        taxonomy="t", input_complete=True, complete=True, skipped_checks={}, thresholds={}, counts={},
        omitted_counts={}, tags=tags, hierarchy=[], candidates=list(candidates),
    )
    return ResolutionReport(
        baseline_snapshot_id="s", evidence=evidence, decisions=[], hierarchy=[], tags=[], mappings=[], links=[],
        dispositions=dispositions, counts={}, partial=True,
    )


def _held(tag_id: str) -> SourceDisposition:
    return SourceDisposition(source=_ref(tag_id), status="pending", reason="Held", reason_codes=["producer_flagged"])


def _accepted(tag_id: str) -> SourceDisposition:
    return SourceDisposition(source=_ref(tag_id), status="accepted", reason="Automatically accepted")


def _saturation(**concept: object) -> SaturationReport:
    entry = {"recommended_action": "REVIEW_AMBIGUOUS", "incoming_ids": ["1"], **concept}
    step = {"by_concept_level": {"tags": {"ambiguous_concepts": [entry]}}}
    return SaturationReport(run_id="r", incoming="demo", step=step)


BASELINE = TaxonomyDataset(
    name="b", services=[], categories=[], specialities=[], speciality_tags=[],
    tags=[{"id": 7, "name": "Payroll"}, {"id": 8, "name": "Shared"}, {"id": 9, "name": "shared"}],
)


def test_automatically_accepted_tags_are_left_out_unless_a_reviewer_decided_them() -> None:
    resolution = _resolution([_held("1"), _accepted("2")])

    assert [item.tag for item in build_review_queue(resolution, None, BASELINE).tags] == ["tag 1"]
    decided = build_review_queue(resolution, None, BASELINE, decided_sources={("demo", "2")})
    assert [item.tag for item in decided.tags] == ["tag 1", "tag 2"]


def test_placements_come_most_confident_first() -> None:
    [item] = build_review_queue(_resolution([_held("1")]), None, BASELINE).tags

    assert [place.category for place in item.placements] == ["Higher", "Lower"]


def test_a_closest_match_names_a_baseline_tag_only_when_exactly_one_has_that_name() -> None:
    saturation = _saturation(
        closest_semantic_score=0.8, closest_semantic_baseline_name="payroll", closest_semantic_baseline_level="tags",
        closest_baseline_score=70, closest_baseline_name="Shared", closest_baseline_level="tags",
    )

    [item] = build_review_queue(_resolution([_held("1")]), saturation, BASELINE).tags

    assert [(match.basis, match.score, match.tag_id) for match in item.closest] == [
        ("semantic", 0.8, 7), ("lexical", 0.7, None),
    ]


def test_a_match_at_another_level_is_shown_but_cannot_be_reused_as_a_tag() -> None:
    saturation = _saturation(
        closest_baseline_score=95, closest_baseline_name="Payroll", closest_baseline_level="specialities",
    )

    [item] = build_review_queue(_resolution([_held("1")]), saturation, BASELINE).tags

    assert item.closest[0].tag_id is None


def test_each_side_of_a_duplicate_pair_lists_the_other() -> None:
    pair = DuplicateCandidate(left=_ref("1"), right=_ref("2"), reason="Same name", similarity=0.95)

    items = build_review_queue(_resolution([_held("1"), _held("2")], [pair]), None, BASELINE).tags

    assert [(item.tag, item.duplicates[0].tag) for item in items] == [("tag 1", "tag 2"), ("tag 2", "tag 1")]


def test_a_run_without_hierarchy_screening_has_no_proposals() -> None:
    assert build_review_queue(_resolution([_held("1")]), None, BASELINE).proposals == []
