from pathlib import Path

import pytest

from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.datasets.ssic import SsicRecord, SsicReference
from taxonomy_pipeline.datasets.ssoc import SsocRecord, SsocReference
from taxonomy_pipeline.metrics.breadth import BreadthMetric
from taxonomy_pipeline.metrics.depth import DepthMetric


def datasets():
    return {
        "original": TaxonomyDataset(
            name="original",
            categories=[{"id": 1, "name": "Accounting", "service_id": 1},
                        {"id": 2, "name": "Retail", "service_id": 2}],
            specialities=[{"id": 10, "name": "Accountants"}],
        ),
        "ssic": SsicReference(source_path=Path("ssic.xlsx"), records=[
            SsicRecord(code="01", title="Accounting", level="division", parent_code="A"),
            SsicRecord(code="02", title="Retail", level="division", parent_code="A"),
            SsicRecord(code="A", title="Accounting", level="section", parent_code=None),
        ]),
        "ssoc": SsocReference(source_path=Path("ssoc.xlsx"), records=[
            SsocRecord(code="241", title="Accountants", level="minor_group", parent_code="24"),
            SsocRecord(code="242", title="Nurses", level="minor_group", parent_code="24"),
        ]),
    }


def run(data=None, **params):
    return BreadthMetric().run(
        datasets() if data is None else data,
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
    data = datasets()
    data["original"] = TaxonomyDataset(name="original")
    result = run(data, threshold=0)
    assert result.details["industry"]["coverage"] == 0
    assert result.details["occupational"]["coverage"] == 0


def test_distinct_codes_and_duplicate_target_names():
    data = datasets()
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
    assert BreadthMetric().run(datasets(), {"target": "ssic"}).status == "error"
    assert DepthMetric().run(datasets(), {"dataset": "ssic"}).status == "error"


@pytest.mark.parametrize("profile", ["typography_v1", "legacy"])
def test_profiles_keep_counts_ties_and_threshold_boundaries(profile):
    data = datasets()
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
    data = datasets()
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
