from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.metrics import saturation
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


def test_semantic_candidates_are_reported_separately_from_lexical_matches(monkeypatch):
    class FakeSemanticMatcher:
        def __init__(self, model_name):
            assert model_name == "test-model"

        def best_matches(self, queries, choices):
            assert choices
            results = {
                "marine services": ("financial services", 0.20),
                "payroll process": ("payroll processing", 0.91),
                "carbon accounting": ("payroll processing", 0.50),
                "carbon reporting": ("accounting", 0.81),
            }
            return [results[query] for query in queries]

    monkeypatch.setattr(saturation, "SemanticMatcher", FakeSemanticMatcher)
    result = SaturationMetric().run(
        _datasets(),
        {
            "baseline": "original",
            "incoming": "skillsfuture",
            "threshold": 95,
            "semantic_model": "test-model",
            "semantic_threshold": 0.8,
        },
    )

    assert result.status == "ok"
    overall = result.details["overall"]
    # The two semantic candidates are represented in the review result;
    # Marine Services and Carbon Accounting remain candidate-new.
    assert overall == {"relevant_concepts_reviewed": 6, "candidate_new": 2, "new_concept_rate": 0.333}
    levels = result.details["by_concept_level"]
    assert levels["specialities"]["semantic_candidate_matches"] == 1
    assert levels["tags"]["semantic_match_details"][0]["incoming_name"] == "Carbon Reporting"


def test_invalid_semantic_threshold_reports_an_error():
    result = SaturationMetric().run(
        _datasets(),
        {"baseline": "original", "incoming": "skillsfuture", "semantic_threshold": 1.1},
    )
    assert result.status == "error"
