from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.metrics.saturation import SaturationMetric


def _datasets() -> dict[str, TaxonomyDataset]:
    return {
        "original": TaxonomyDataset(
            name="original",
            categories=[{"id": 1, "name": "Financial Services"}],
            specialities=[{"id": 10, "name": "Payroll Processing"}],
            tags=[{"id": 100, "name": "accounting"}],
        ),
        "skillsfuture": TaxonomyDataset(
            name="skillsfuture",
            categories=[
                {"id": 1, "name": "Financial Services"},
                {"id": 2, "name": "Marine Services"},
            ],
            specialities=[
                {"id": 10, "name": "Payroll Process"},
                {"id": 11, "name": "Carbon Accounting"},
            ],
            tags=[
                {"id": 100, "name": "Accounting"},
                {"id": 101, "name": "Carbon Reporting"},
                {"id": 102, "name": "Carbon Reporting"},
            ],
        ),
    }


def test_compares_incoming_taxonomy_and_reports_candidate_new_concepts():
    result = SaturationMetric().run(
        _datasets(), {"baseline": "original", "incoming": "skillsfuture", "threshold": 90}
    )

    assert result.status == "ok"
    overall = result.details["overall"]
    # Six distinct incoming concepts: 2 categories, 2 specialities, 2 tags.
    assert overall == {"relevant_concepts_reviewed": 6, "candidate_new": 3, "new_concept_rate": 0.5}

    levels = result.details["by_concept_level"]
    assert levels["categories"]["exact_matches"] == 1
    assert levels["specialities"]["fuzzy_candidate_matches"] == 1
    assert [concept["incoming_name"] for concept in levels["tags"]["candidate_new_concepts"]] == [
        "Carbon Reporting"
    ]


def test_missing_datasets_report_an_error():
    result = SaturationMetric().run(
        _datasets(), {"baseline": "original", "incoming": "not-loaded"}
    )
    assert result.status == "error"


def test_invalid_threshold_reports_an_error():
    result = SaturationMetric().run(
        _datasets(), {"baseline": "original", "incoming": "skillsfuture", "threshold": 101}
    )
    assert result.status == "error"
