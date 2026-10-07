"""Bulk decisions settle the clear-cut ambiguous tags by saturation similarity and leave the rest to a person."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from taxonomy_pipeline.classification.bulk_decisions import draft_decisions
from taxonomy_pipeline.classification.models import ReviewDecision, SourceTagRef, TaxonomyPath
from taxonomy_pipeline.classification.routing import load_saturation, mark_ambiguous
from taxonomy_pipeline.classification.runner import run_merge_preview
from taxonomy_pipeline.datasets.base import TaxonomyDataset

FIXTURES = Path(__file__).parent / "fixtures" / "classification"


def _concept(tag_id: int, score: float, name: str, level: str) -> dict[str, object]:
    return {
        "recommended_action": "REVIEW_AMBIGUOUS", "parent_status": "NO_PARENT", "incoming_ids": [tag_id],
        "reason": "partial_similarity", "closest_semantic_baseline_name": name, "closest_semantic_score": score,
        "closest_semantic_baseline_level": level,
    }


CONCEPTS = [
    _concept(11, 0.31, "Data presentation", "specialities"),  # loosely similar: create
    _concept(12, 0.93, "SQL", "tags"),  # near-certainly the existing tag: reuse
    _concept(13, 0.62, "Data presentation", "specialities"),  # middle band: a person decides
    _concept(15, 0.95, "Programming", "specialities"),  # close, but to a speciality, so nothing to reuse
    {"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [14]},
]


@pytest.fixture
def baseline() -> TaxonomyDataset:
    return TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))


def _record(tag_id: str, tag: str, category: str, speciality: str, confidence: float) -> dict[str, object]:
    return {
        "source_dataset": "skillsfuture", "tag_id": tag_id, "tag": tag, "original_classification": "unclassified",
        "service": "Subject", "category": category, "speciality": speciality, "confidence": confidence,
        "reason": "Proposed.",
    }


@pytest.fixture
def merge(tmp_path, baseline, saturation_report):
    """A merge-preview over producer output for the tags above, flagged the way Case A/B flag them."""
    report = load_saturation(saturation_report(CONCEPTS), "skillsfuture")
    records = mark_ambiguous([
        _record("11", "Fire Prevention", "Biology", "Zoology", 0.4),
        _record("11", "Fire Prevention", "Data analytics", "Data presentation", 0.7),
        _record("12", "Structured Query Language", "Data analytics", "Data presentation", 0.6),
        _record("13", "Data storytelling", "Data analytics", "Data presentation", 0.6),
        _record("14", "Spreadsheet Modelling", "Data analytics", "Data presentation", 0.6),
        _record("15", "Python scripting", "Data analytics", "Programming", 0.6),
    ], report)
    path = tmp_path / "producer.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    def run(decisions: list[ReviewDecision] | None = None, name: str = "merge"):
        return run_merge_preview([path], baseline, out_dir=tmp_path / name, decisions=decisions or [])

    return report, run


def test_creates_loose_matches_and_reuses_near_certain_tags(baseline, merge):
    report, run = merge
    draft = draft_decisions(run().preview.resolution, report, baseline, create_below=0.5, reuse_at_or_above=0.9)

    by_tag = {decision.members[0].tag_id: decision for decision in draft.drafted}
    assert set(by_tag) == {"11", "12"}
    assert by_tag["11"].action == "create"
    assert by_tag["11"].approved_paths == [  # the higher-confidence of its two placements
        TaxonomyPath(service="Subject", category="Data analytics", speciality="Data presentation"),
    ]
    assert by_tag["12"].action == "reuse" and by_tag["12"].existing_tag_id == 200
    assert "semantic similarity 0.93 to existing tag 'SQL'" in by_tag["12"].reason
    assert draft.counts == {"create": 1, "reuse": 1, "left_for_review": 2}


def test_applying_the_draft_leaves_only_the_middle_band_pending(baseline, merge):
    report, run = merge
    first = run()
    assert first.preview.counts.pending_sources == 4

    draft = draft_decisions(first.preview.resolution, report, baseline, create_below=0.5, reuse_at_or_above=0.9)
    reviewed = run(draft.decisions, name="reviewed")

    pending = {d.source.tag_id for d in reviewed.preview.resolution.dispositions if d.status == "pending"}
    assert pending == {"13", "15"}
    assert reviewed.preview.counts.reused_tags == 1  # SQL, now also linked under Data presentation


def test_keeps_earlier_decisions_and_never_redecides_their_tags(baseline, merge):
    report, run = merge
    by_hand = ReviewDecision(members=[SourceTagRef(source_dataset="skillsfuture", tag_id="11")], action="exclude",
                             reason="Not a skill.")

    draft = draft_decisions(run().preview.resolution, report, baseline, create_below=0.5, existing=[by_hand])

    assert draft.decisions[0] == by_hand
    assert [decision.members[0].tag_id for decision in draft.drafted] == []
    assert draft.counts["already_decided"] == 1


def test_leaves_tags_held_for_a_duplicate_pair_to_a_person(tmp_path, baseline, saturation_report):
    report = load_saturation(saturation_report([_concept(21, 0.2, "Zoology", "specialities"),
                                                _concept(22, 0.2, "Zoology", "specialities")]), "skillsfuture")
    records = mark_ambiguous([_record("21", "Fire Safety", "Biology", "Zoology", 0.6),
                              _record("22", "Fire safety", "Biology", "Zoology", 0.6)], report)
    path = tmp_path / "producer.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    resolution = run_merge_preview([path], baseline, out_dir=tmp_path / "merge").preview.resolution

    draft = draft_decisions(resolution, report, baseline, create_below=0.5)

    assert draft.drafted == []
    assert draft.counts["held_for_another_reason"] == 2


def test_a_strong_spelling_match_keeps_a_tag_for_review_despite_a_weak_meaning_match(
    tmp_path, baseline, saturation_report,
):
    # Meaning only loosely matches a speciality, but the spelling is nearly the baseline tag "SQL".
    concept = {**_concept(31, 0.2, "Zoology", "specialities"),
               "closest_baseline_name": "SQL", "closest_baseline_score": 95.0, "closest_baseline_level": "tags"}
    report = load_saturation(saturation_report([concept]), "skillsfuture")
    path = tmp_path / "producer.json"
    path.write_text(json.dumps(mark_ambiguous(
        [_record("31", "SQL querying", "Data analytics", "Programming", 0.6)], report,
    )), encoding="utf-8")
    resolution = run_merge_preview([path], baseline, out_dir=tmp_path / "merge").preview.resolution

    draft = draft_decisions(resolution, report, baseline, create_below=0.5, reuse_at_or_above=0.9)

    assert draft.drafted == []  # neither created from the weak score nor reused on spelling alone
    assert draft.counts["left_for_review"] == 1


def test_refuses_a_baseline_that_changed_since_the_merge_run(baseline, merge):
    report, run = merge
    resolution = run().preview.resolution
    baseline.tags.append({"id": 999, "name": "Added later"})

    with pytest.raises(ValueError, match="baseline changed since this merge run"):
        draft_decisions(resolution, report, baseline, create_below=0.5)


@pytest.mark.parametrize("kwargs", [
    {}, {"create_below": 0.9, "reuse_at_or_above": 0.5}, {"create_below": 2.0}, {"reuse_at_or_above": -0.1},
    {"create_below": float("nan")},
])
def test_rejects_missing_out_of_range_or_overlapping_thresholds(baseline, merge, kwargs):
    report, run = merge
    with pytest.raises(ValueError):
        draft_decisions(run().preview.resolution, report, baseline, **kwargs)
