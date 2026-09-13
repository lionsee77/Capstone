from __future__ import annotations

from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.metrics.breadth import BreadthMetric


def _datasets() -> dict[str, TaxonomyDataset]:
    target = TaxonomyDataset(
        name="original",
        categories=[
            {"id": 1, "name": "Accounting"},
            {"id": 2, "name": "Marketing"},
        ],
    )
    reference = TaxonomyDataset(
        name="skillsfuture",
        categories=[
            {"id": 1, "name": "Accountancy"},  # close to "Accounting"
            {"id": 2, "name": "Retail"},  # nothing close in target
        ],
    )
    return {"original": target, "skillsfuture": reference}


def test_threshold_zero_covers_everything():
    result = BreadthMetric().run(
        _datasets(), {"target": "original", "reference": "skillsfuture", "threshold": 0}
    )
    assert result.details["coverage"] == 1.0
    assert result.details["unmatched_reference_categories"] == []


def test_threshold_hundred_requires_exact_match():
    result = BreadthMetric().run(
        _datasets(), {"target": "original", "reference": "skillsfuture", "threshold": 100}
    )
    assert result.details["coverage"] == 0.0
    assert set(result.details["unmatched_reference_categories"]) == {"Accountancy", "Retail"}


def test_missing_dataset_reports_error():
    result = BreadthMetric().run({}, {"target": "original", "reference": "skillsfuture"})
    assert result.status == "error"
