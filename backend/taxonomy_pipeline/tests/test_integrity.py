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


def test_normalize_matches_taxonomy_audit_baseline_folding():
    # Same folding steps as backend/audit/taxonomy_audit.py's normalized()
    # (NFKC, casefold, dash-variant folding, trailing-punctuation strip) -
    # duplicated on purpose (see integrity.py's normalize() docstring), so
    # this pipeline's dup counts stay comparable to that original baseline.
    dataset = TaxonomyDataset(
        name="original",
        categories=[
            {"id": 1, "name": "Mergers–Acquisitions"},  # en dash
            {"id": 2, "name": "Mergers-Acquisitions."},  # hyphen + trailing period
        ],
    )
    result = IntegrityMetric().run({"original": dataset}, {"dataset": "original"})
    clusters = result.details["exact_duplicates"]["categories"]
    assert len(clusters) == 1
    assert set(clusters[0]["ids"]) == {1, 2}


def test_skips_orphan_category_check_when_no_services_loaded():
    # _dataset() has categories pointing at service_id=1 but loads no services,
    # so the check can't run - it must say so rather than report an empty pass.
    result = IntegrityMetric().run({"original": _dataset()}, {"dataset": "original"})
    assert result.status == "ok"
    assert result.details["orphans"]["categories_without_service"] is None
    assert "categories_without_service" in result.details["skipped_checks"]
    assert "Skipped: categories_without_service" in result.summary


def test_finds_category_whose_service_is_missing():
    dataset = TaxonomyDataset(
        name="original",
        services=[{"id": 1, "name": "Industry"}],
        categories=[
            {"id": 1, "name": "Accounting", "service_id": 1},
            {"id": 2, "name": "Marketing", "service_id": 2},  # no such service
        ],
    )
    result = IntegrityMetric().run({"original": dataset}, {"dataset": "original"})
    assert result.details["orphans"]["categories_without_service"] == [2]
    assert result.details["skipped_checks"] == {}
    assert "Skipped" not in result.summary
