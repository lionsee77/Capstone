from __future__ import annotations

from taxonomy_pipeline.quality.depth import DepthMetric
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


def test_branching_factor_stats():
    dataset = TaxonomyDataset(
        name="original",
        categories=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        specialities=[
            {"id": 10, "name": "s1", "category_id": 1},
            {"id": 11, "name": "s2", "category_id": 1},
            {"id": 12, "name": "s3", "category_id": 2},
        ],
        tags=[{"id": 100, "name": "t1"}],
        speciality_tags=[
            {"id": 1000, "speciality_id": 10, "tag_id": 100},
            {"id": 1001, "speciality_id": 11, "tag_id": 100},
        ],
    )
    result = DepthMetric().run({"original": dataset}, {"dataset": "original"})

    cat_to_spec = result.details["category_to_speciality"]
    assert cat_to_spec == {"min": 1, "max": 2, "mean": 1.5, "median": 1.5, "n": 2}

    spec_to_tag = result.details["speciality_to_tag"]
    # 3 specialities: two with one tag each, one (s3) with zero.
    assert spec_to_tag == {"min": 0, "max": 1, "mean": 0.67, "median": 1, "n": 3}


def test_dangling_tag_link_is_not_counted_as_a_real_tag():
    dataset = TaxonomyDataset(
        name="original",
        categories=[{"id": 1, "name": "A"}],
        specialities=[{"id": 10, "name": "s1", "category_id": 1}],
        tags=[{"id": 100, "name": "t1"}],
        speciality_tags=[
            {"id": 1000, "speciality_id": 10, "tag_id": 100},  # real link
            {"id": 1001, "speciality_id": 10, "tag_id": 999},  # dangling: no such tag
        ],
    )
    result = DepthMetric().run({"original": dataset}, {"dataset": "original"})
    spec_to_tag = result.details["speciality_to_tag"]
    # Only the one real link should count, not both.
    assert spec_to_tag == {"min": 1, "max": 1, "mean": 1, "median": 1, "n": 1}


def test_empty_dataset_reports_zeroed_stats_not_a_crash():
    result = DepthMetric().run({"empty": TaxonomyDataset(name="empty")}, {"dataset": "empty"})
    assert result.status == "ok"
    assert result.details["category_to_speciality"]["n"] == 0
