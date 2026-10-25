"""One saturation report splits new and ambiguous tags between Case A and Case B without overlap."""
from __future__ import annotations

import json

import pytest

from taxonomy_pipeline.enrichment.routing import case_a_tag_ids, case_b_tag_ids, load_saturation, mark_ambiguous

CONCEPTS = [
    {"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [100, 100]},
    {"recommended_action": "ADD_CANDIDATE", "parent_status": "HAS_PARENT", "incoming_ids": [101, 104]},
    {"recommended_action": "SKIP_DUPLICATE", "parent_status": "NO_PARENT", "incoming_ids": [102]},
    {"recommended_action": "REVIEW_AMBIGUOUS", "parent_status": "NO_PARENT", "incoming_ids": [103],
     "reason": "partial_similarity", "closest_semantic_baseline_name": "Product Management",
     "closest_semantic_score": 0.7, "closest_semantic_baseline_level": "specialities",
     "closest_baseline_name": "Product Marketing", "closest_baseline_score": 81.0, "closest_baseline_level": "tags"},
    {"recommended_action": "REVIEW_AMBIGUOUS", "parent_status": "HAS_PARENT", "incoming_ids": [105]},
    {"recommended_action": "REVIEW_NEW_PLACEMENT", "parent_status": "HAS_PARENT", "incoming_ids": [106]},
]


def test_splits_new_and_ambiguous_tags_by_parent_status_without_overlap(saturation_report):
    report = load_saturation(saturation_report(CONCEPTS), "skillsfuture")

    assert case_a_tag_ids(report) == [101, 104, 105]
    assert case_b_tag_ids(report) == [100, 103]
    assert report.run_id == "run-1"


def test_never_routes_duplicates_or_other_review_outcomes(saturation_report):
    report = load_saturation(saturation_report(CONCEPTS), "skillsfuture")

    routed = set(case_a_tag_ids(report)) | set(case_b_tag_ids(report))
    assert routed.isdisjoint({102, 106})


def _record(tag_id: str, *, placed: bool = True) -> dict[str, object]:
    path = ("Subject", "Data analytics", "Product Testing", 0.5) if placed else (None, None, None, None)
    return {
        "source_dataset": "skillsfuture", "tag_id": tag_id, "tag": "Product Testing Management",
        "original_classification": "unclassified", **dict(zip(("service", "category", "speciality", "confidence"),
                                                               path, strict=True)),
        "reason": "Proposed.",
    }


def test_flags_ambiguous_placements_for_review_with_what_they_resemble(saturation_report):
    report = load_saturation(saturation_report(CONCEPTS), "skillsfuture")

    [marked] = mark_ambiguous([_record("103")], report)

    assert marked["needs_review"] is True
    assert marked["reason"].startswith("Proposed. Needs review: saturation could not confirm this tag is new")
    assert "speciality 'Product Management' in meaning (0.7)" in marked["reason"]
    assert "tag 'Product Marketing' in spelling (81.0)" in marked["reason"]


def test_leaves_new_tags_and_unplaced_records_unflagged(saturation_report):
    report = load_saturation(saturation_report(CONCEPTS), "skillsfuture")
    records = [_record("100"), _record("103", placed=False)]

    assert mark_ambiguous(records, report) == records  # nothing to approve on an unplaced record


def test_missing_report_explains_how_to_generate_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="taxonomy-pipeline run"):
        load_saturation(tmp_path / "missing.json", "skillsfuture")


def test_malformed_report_is_rejected(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"results": [{"metric_id": "integrity"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid saturation report"):
        load_saturation(path, "skillsfuture")


def test_report_for_another_incoming_source_is_rejected(saturation_report):
    with pytest.raises(ValueError, match="no results for incoming source 'skillsfuture'"):
        load_saturation(saturation_report(CONCEPTS, incoming="onet"), "skillsfuture")


def test_reads_the_requested_source_from_a_multi_source_curve(tmp_path):
    def step(source, tag_id):
        concept = {"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [tag_id]}
        return {"source": source, "by_concept_level": {"tags": {"ambiguous_concepts": [concept]}}}

    path = tmp_path / "report.json"
    steps = [step("skillsfuture", 1), step("onet", 2)]
    path.write_text(json.dumps({"run_id": "r", "results": [{"metric_id": "saturation", "details": {
        "incoming": ["skillsfuture", "onet"], "by_concept_level": steps[-1]["by_concept_level"], "steps": steps,
    }}]}), encoding="utf-8")

    assert case_b_tag_ids(load_saturation(path, "skillsfuture")) == [1]


def test_empty_selection_is_an_error_for_either_case(saturation_report):
    report = load_saturation(saturation_report([CONCEPTS[2], CONCEPTS[5]]), "skillsfuture")

    with pytest.raises(ValueError, match="No Case A tags"):
        case_a_tag_ids(report)
    with pytest.raises(ValueError, match="No Case B tags"):
        case_b_tag_ids(report)


def test_reads_a_report_written_before_per_source_steps_existed(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"run_id": "old", "results": [{"metric_id": "saturation", "details": {
        "incoming": "skillsfuture", "by_concept_level": {"tags": {"ambiguous_concepts": CONCEPTS}},
    }}]}), encoding="utf-8")

    report = load_saturation(path, "skillsfuture")

    assert case_b_tag_ids(report) == [100, 103]
