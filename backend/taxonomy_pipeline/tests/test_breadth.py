from pathlib import Path

import pytest

from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.datasets.ssic import SsicReference, SsicRecord
from taxonomy_pipeline.datasets.ssoc import SsocReference, SsocRecord
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
    return BreadthMetric().run(datasets() if data is None else data, {"target": "original", **params})


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
