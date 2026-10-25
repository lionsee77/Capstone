"""Case A places tags chosen by a saved saturation report and stamps the run it used."""
from __future__ import annotations

import pytest

from taxonomy_pipeline.enrichment.case_a import CaseAError, run_case_a
from taxonomy_pipeline.enrichment.models import CLASSIFICATION_OUTPUTS
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


def _datasets() -> dict[str, TaxonomyDataset]:
    return {
        "original": TaxonomyDataset(
            name="original",
            services=[{"id": 1, "name": "Industry"}],
            categories=[{"id": 1, "name": "Banking", "service_id": 1}],
            specialities=[{"id": 1, "name": "Retail Banking", "category_id": 1}],
        ),
        "skillsfuture": TaxonomyDataset(
            name="skillsfuture",
            categories=[{"id": 501, "name": "Banking", "service_id": 1}],
            specialities=[{"id": 601, "name": "Retail Banking", "category_id": 501}],
            tags=[{"id": 9001, "name": "loan processing"}, {"id": 9002, "name": "parentless skill"}],
            speciality_tags=[{"id": 701, "speciality_id": 601, "tag_id": 9001}],
        ),
    }


CONCEPTS = [
    {"recommended_action": "ADD_CANDIDATE", "parent_status": "HAS_PARENT", "incoming_ids": [9001]},
    {"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [9002]},
]


def test_places_only_report_tags_with_a_parent_and_stamps_the_run(saturation_report):
    records = run_case_a(_datasets(), saturation_report(CONCEPTS, run_id="sat-7"),
                         baseline="original", incoming="skillsfuture")

    assert [record["tag_id"] for record in records] == ["9001"]
    assert records[0]["service"] == "Industry"
    assert records[0]["category"] == "Banking"
    assert records[0]["speciality"] == "loan processing"  # the skill itself, not the source speciality it hangs off
    assert records[0]["saturation_run_id"] == "sat-7"
    CLASSIFICATION_OUTPUTS.validate_python(records)


def test_emits_one_record_per_source_category_however_many_source_specialities_link_the_tag(saturation_report):
    datasets = _datasets()
    for offset in range(2, 12):
        datasets["skillsfuture"].specialities.append({"id": 600 + offset, "name": f"Role {offset}", "category_id": 501})
        datasets["skillsfuture"].speciality_tags.append({"id": 700 + offset, "speciality_id": 600 + offset,
                                                         "tag_id": 9001})
    records = run_case_a(datasets, saturation_report(CONCEPTS), baseline="original", incoming="skillsfuture")

    assert len(records) == 1


def test_every_case_a_tag_ends_placed_pending_review_or_unresolved_never_dropped(saturation_report):
    # "Banking Svc" only partly matches "Banking" (spelling 78), so it isn't
    # confirmed: its tag must come out as review candidates, not as an empty
    # record that merge-preview would silently set aside.
    datasets = _datasets()
    datasets["skillsfuture"].categories.append({"id": 502, "name": "Banking Svc", "service_id": 1})
    datasets["skillsfuture"].specialities.append({"id": 602, "name": "Teller", "category_id": 502})
    datasets["skillsfuture"].tags.append({"id": 9003, "name": "cash handling"})
    datasets["skillsfuture"].speciality_tags.append({"id": 702, "speciality_id": 602, "tag_id": 9003})
    concepts = [
        {"recommended_action": "ADD_CANDIDATE", "parent_status": "HAS_PARENT", "incoming_ids": [9001, 9003]},
    ]
    records = run_case_a(datasets, saturation_report(concepts), baseline="original", incoming="skillsfuture")

    outcomes: dict[str, set[str]] = {}
    for record in records:
        outcome = "pending_review" if record.get("needs_review") else "placed" if record["category"] else "unresolved"
        outcomes.setdefault(record["tag_id"], set()).add(outcome)
    assert outcomes == {"9001": {"placed"}, "9003": {"pending_review"}}
    review = next(record for record in records if record["tag_id"] == "9003")
    assert (review["category"], review["speciality"]) == ("Banking", "cash handling")
    CLASSIFICATION_OUTPUTS.validate_python(records)


def test_places_ambiguous_tags_but_flags_every_placement_for_review(saturation_report):
    datasets = _datasets()
    datasets["skillsfuture"].tags.append({"id": 9004, "name": "loan servicing"})
    datasets["skillsfuture"].speciality_tags.append({"id": 703, "speciality_id": 601, "tag_id": 9004})
    concepts = [*CONCEPTS, {
        "recommended_action": "REVIEW_AMBIGUOUS", "parent_status": "HAS_PARENT", "incoming_ids": [9004],
        "reason": "partial_similarity", "closest_semantic_baseline_name": "Loan Origination",
        "closest_semantic_score": 0.64, "closest_semantic_baseline_level": "tags",
    }]
    records = run_case_a(datasets, saturation_report(concepts), baseline="original", incoming="skillsfuture")

    by_tag = {record["tag_id"]: record for record in records}
    assert "needs_review" not in by_tag["9001"]  # ADD_CANDIDATE: accepted by default, as before
    assert by_tag["9004"]["needs_review"] is True
    assert (by_tag["9004"]["category"], by_tag["9004"]["speciality"]) == ("Banking", "loan servicing")
    assert "tag 'Loan Origination' in meaning (0.64)" in by_tag["9004"]["reason"]
    CLASSIFICATION_OUTPUTS.validate_python(records)


def test_requires_a_saturation_report(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_case_a(_datasets(), tmp_path / "report.json", baseline="original", incoming="skillsfuture")


def test_reports_category_adaptation_failures(saturation_report):
    with pytest.raises(CaseAError, match="category_adaptation failed"):
        run_case_a(_datasets(), saturation_report(CONCEPTS), baseline="missing", incoming="skillsfuture")
