from __future__ import annotations

from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.metrics.integrity import IntegrityMetric


def _dataset() -> TaxonomyDataset:
    return TaxonomyDataset(
        name="original",
        categories=[
            {"id": 1, "name": "Accounting", "service_id": 1},
            {"id": 2, "name": "accounting", "service_id": 1},  # exact dup of 1 (case-insensitive)
            {"id": 3, "name": "Marketing", "service_id": 1},
        ],
        specialities=[
            {"id": 10, "name": "Tax Advisory", "category_id": 1},
            {"id": 11, "name": "Brand Strategy", "category_id": 3},  # zero tags -> dead end
            {"id": 12, "name": "Orphan Speciality", "category_id": 999},  # missing category
        ],
        tags=[
            {"id": 100, "name": "tax"},
            {"id": 101, "name": "gst"},
            {"id": 102, "name": "unused-tag"},  # never linked -> orphan
        ],
        speciality_tags=[
            {"id": 1000, "speciality_id": 10, "tag_id": 100},
            {"id": 1001, "speciality_id": 10, "tag_id": 101},
            {"id": 1002, "speciality_id": 999, "tag_id": 100},  # dangling: no such speciality
        ],
    )


def test_finds_exact_duplicate_category_cluster():
    result = IntegrityMetric().run({"original": _dataset()}, {"dataset": "original"})
    assert result.status == "ok"
    clusters = result.details["exact_duplicates"]["categories"]
    assert len(clusters) == 1
    assert set(clusters[0]["ids"]) == {1, 2}


def test_finds_orphan_speciality_and_unused_tag():
    result = IntegrityMetric().run({"original": _dataset()}, {"dataset": "original"})
    assert result.details["orphans"]["specialities_without_category"] == [12]
    assert result.details["orphans"]["tags_unused_by_any_speciality"] == [102]
    assert result.details["orphans"]["dangling_speciality_tag_links"] == 1


def test_finds_dead_end_category_and_speciality():
    result = IntegrityMetric().run({"original": _dataset()}, {"dataset": "original"})
    # category 2 ('accounting') has no specialities of its own, category 3 has
    # speciality 11 but that speciality has zero tags.
    assert 2 in result.details["dead_ends"]["categories_with_zero_specialities"]
    assert 11 in result.details["dead_ends"]["specialities_with_zero_tags"]


def test_unknown_dataset_reports_error_not_exception():
    result = IntegrityMetric().run({}, {"dataset": "missing"})
    assert result.status == "error"
