"""One saturation report splits new tags between Case A and Case B without overlap."""
from __future__ import annotations

import json

import pytest

from taxonomy_pipeline.classification.routing import case_a_tag_ids, case_b_tag_ids, load_saturation

CONCEPTS = [
    {"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [100, 100]},
    {"recommended_action": "ADD_CANDIDATE", "parent_status": "HAS_PARENT", "incoming_ids": [101, 104]},
    {"recommended_action": "SKIP_DUPLICATE", "parent_status": "NO_PARENT", "incoming_ids": [102]},
    {"recommended_action": "REVIEW_AMBIGUOUS", "parent_status": "NO_PARENT", "incoming_ids": [103]},
]


def test_splits_add_candidates_by_parent_status_without_overlap(saturation_report):
    report = load_saturation(saturation_report(CONCEPTS), "skillsfuture")

    assert case_a_tag_ids(report) == [101, 104]
    assert case_b_tag_ids(report) == [100]
    assert report.run_id == "run-1"


def test_never_routes_duplicates_or_ambiguous_tags(saturation_report):
    report = load_saturation(saturation_report(CONCEPTS), "skillsfuture")

    routed = set(case_a_tag_ids(report)) | set(case_b_tag_ids(report))
    assert routed.isdisjoint({102, 103})


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
    report = load_saturation(saturation_report(CONCEPTS[2:]), "skillsfuture")

    with pytest.raises(ValueError, match="No Case A tags"):
        case_a_tag_ids(report)
    with pytest.raises(ValueError, match="No Case B tags"):
        case_b_tag_ids(report)
