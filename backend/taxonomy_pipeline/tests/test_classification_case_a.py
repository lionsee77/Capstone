"""Case A places tags chosen by a saved saturation report and stamps the run it used."""
from __future__ import annotations

import pytest

from taxonomy_pipeline.classification.case_a import CaseAError, run_case_a
from taxonomy_pipeline.classification.models import CLASSIFICATION_OUTPUTS
from taxonomy_pipeline.datasets.base import TaxonomyDataset


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


def test_requires_a_saturation_report(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_case_a(_datasets(), tmp_path / "report.json", baseline="original", incoming="skillsfuture")


def test_reports_category_adaptation_failures(saturation_report):
    with pytest.raises(CaseAError, match="category_adaptation failed"):
        run_case_a(_datasets(), saturation_report(CONCEPTS), baseline="missing", incoming="skillsfuture")
