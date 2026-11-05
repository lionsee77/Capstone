
import pytest

from taxonomy_pipeline.quality.breadth import BreadthMetric
from taxonomy_pipeline.quality.depth import DepthMetric
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset
from taxonomy_pipeline.taxonomy.references.ssic import SsicRecord
from tests.conftest import breadth_datasets


def run(data=None, **params):
    return BreadthMetric().run(
        breadth_datasets() if data is None else data,
        {"target": "original",
         "matching_method": "lexical",
         **params}
    )


def test_both_dimensions_and_industry_filter():
    result = run(threshold=100)
    assert result.status == "ok"
    for dimension in ("industry", "occupational"):
        detail = result.details[dimension]
        assert detail["coverage"] == 0.5
        assert detail["matched_count"] == 1
        assert detail["total_count"] == 2
        assert detail["target_node_count"] == 1
        assert detail["matched"][0]["score"] == 100
        assert len(detail["unmatched"]) == 1
    assert result.details["industry"]["matched"][0]["target_id"] == 1
    assert result.details["occupational"]["matched"][0]["code"] == "241"
    assert "estimated lexical coverage" in result.summary


def test_threshold_zero_and_overridden_service_filter():
    assert run(threshold=0).details["industry"]["coverage"] == 1
    result = run(threshold=100, industry_service_id=2)
    assert result.details["industry"]["matched"][0]["matched_to"] == "Retail"


def test_selectable_and_empty_levels():
    result = run(industry_level="section", occupational_level="unit_group", threshold=100)
    assert result.details["industry"]["coverage"] == 1
    assert result.details["occupational"]["coverage"] is None
    assert "no reference records" in result.summary


def test_empty_targets_do_not_match_even_at_zero_threshold():
    data = breadth_datasets()
    data["original"] = TaxonomyDataset(name="original")
    result = run(data, threshold=0)
    assert result.details["industry"]["coverage"] == 0
    assert result.details["occupational"]["coverage"] == 0


def test_distinct_codes_and_duplicate_target_names():
    data = breadth_datasets()
    data["original"].categories.append(dict(data["original"].categories[0], id=3))
    data["ssic"].records.append(data["ssic"].records[0])
    data["ssic"].records.append(SsicRecord(
        code="03", title="Accounting", level="division", parent_code="A"
    ))
    result = run(data, threshold=100).details["industry"]
    assert result["total_count"] == 3
    assert result["matched_count"] == 2
    assert {r["target_id"] for r in result["matched"]} == {1}


@pytest.mark.parametrize("params", [
    {"threshold": -1}, {"threshold": 101}, {"threshold": float("nan")},
    {"threshold": float("inf")}, {"threshold": "bad"}, {"threshold": None},
    {"industry_level": "occupation"}, {"occupational_level": "division"},
    {"reference": "skillsfuture"},
])
def test_invalid_params(params):
    assert run(**params).status == "error"


def test_missing_and_wrong_dataset_types():
    assert run({}).status == "error"
    assert run(ssic_reference="ssoc").status == "error"
    assert run(ssoc_reference="original").status == "error"
    assert BreadthMetric().run(breadth_datasets(), {"target": "ssic"}).status == "error"
    assert DepthMetric().run(breadth_datasets(), {"dataset": "ssic"}).status == "error"


@pytest.mark.parametrize("profile", ["typography_v1", "legacy"])
def test_profiles_keep_counts_ties_and_threshold_boundaries(profile):
    data = breadth_datasets()
    data["original"].categories.append({"id": 3, "name": "ACCOUNTING", "service_id": 1})
    result = run(data, threshold=100, preprocessing=profile)
    detail = result.details["industry"]
    assert detail["target_node_count"] == 2
    assert detail["matched_count"] == 1
    assert detail["matched"][0]["target_id"] == 1
    assert run(data, threshold=0, preprocessing=profile).details["industry"]["coverage"] == 1
    data["original"] = TaxonomyDataset(name="original")
    assert run(data, threshold=0, preprocessing=profile).details["industry"]["coverage"] == 0


def test_typography_matches_both_sides_without_mutation():
    data = breadth_datasets()
    data["ssic"].records[0].title = "POST-MARKET / SERVICES"
    data["original"].categories[0]["name"] = "Post‑Market/Services"
    data["original"].categories.append({"id": 3, "name": "POST-MARKET / SERVICES", "service_id": 1})
    before = {name: ds.model_dump() for name, ds in data.items()}
    result = run(data, threshold=100)
    detail = result.details["industry"]
    match = detail["matched"][0]
    assert result.details["preprocessing"] == "typography_v1"
    assert detail["target_node_count"] == 2
    assert match["target_id"] == 1
    assert match["score"] == 100
    assert match["title"] == "POST-MARKET / SERVICES"
    assert match["matched_to"] == "Post‑Market/Services"
    assert match["normalized_reference"] == match["normalized_target"] == "post-market/services"
    assert {name: ds.model_dump() for name, ds in data.items()} == before
    legacy = run(data, threshold=100, preprocessing="legacy")
    assert legacy.details["industry"]["matched"][0]["target_id"] == 3


@pytest.mark.parametrize("profile", [None, "unknown", 1, [], {}])
def test_invalid_preprocessing_is_metric_error(profile):
    result = run(preprocessing=profile)
    assert result.status == "error"
    assert "preprocessing" in result.summary


def test_measures_report_covered_nodes_and_coverage_with_higher_as_better():
    from tests.conftest import baseline_of_record

    result = next(r for r in baseline_of_record().results if r.metric_id == "breadth")
    measures = {m.key: m for m in BreadthMetric().measures(result)}

    assert measures["industry.matched_count"].value == 18
    assert measures["occupational.matched_count"].value == 16
    assert measures["occupational.coverage"].value == 0.11
    assert {m.better for m in measures.values()} == {"higher"}
