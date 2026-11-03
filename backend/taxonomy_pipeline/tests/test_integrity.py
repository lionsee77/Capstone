from __future__ import annotations

from taxonomy_pipeline.quality.integrity import IntegrityMetric
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


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


def test_reports_orphan_categories_when_services_table_is_present_but_empty():
    # An empty services table is still a table: every category pointing at a
    # service is an orphan, not a reason to skip the check.
    dataset = TaxonomyDataset(
        name="original",
        services=[],
        categories=[{"id": 1, "name": "Accounting", "service_id": 999}],
    )
    result = IntegrityMetric().run({"original": dataset}, {"dataset": "original"})
    assert result.details["orphans"]["categories_without_service"] == [1]
    assert result.details["skipped_checks"] == {}


def test_measures_count_every_defect_in_the_baseline_of_record_with_lower_as_better():
    from tests.conftest import baseline_of_record

    result = next(r for r in baseline_of_record().results if r.metric_id == "integrity")
    measures = {m.key: m for m in IntegrityMetric().measures(result)}

    # The baseline's summary: 53 rows in exact-duplicate clusters, 1 unused tag, 14 / 1 dead ends.
    exact = sum(measures[f"exact_duplicate_rows.{level}"].value for level in ("categories", "specialities", "tags"))
    assert exact == 53
    assert measures["orphans.tags_unused_by_any_speciality"].value == 1
    assert measures["dead_ends.categories_with_zero_specialities"].value == 14
    assert measures["fuzzy_duplicate_pairs.tags"].value == 57
    assert {m.better for m in measures.values()} == {"lower"}


def test_a_skipped_orphan_check_has_no_measure_value():
    ds = TaxonomyDataset(
        name="t",
        categories=[{"id": 1, "name": "Design"}],
        specialities=[{"id": 10, "name": "Logo", "category_id": 1}],
        tags=[{"id": 100, "name": "Illustrator"}],
        speciality_tags=[{"id": 1, "speciality_id": 10, "tag_id": 100}],
    )
    result = IntegrityMetric().run({"t": ds}, {"dataset": "t"})

    measures = {m.key: m.value for m in IntegrityMetric().measures(result)}
    assert measures["orphans.categories_without_service"] is None
    assert measures["orphans.specialities_without_category"] == 0


def test_a_level_or_section_missing_from_stored_details_has_no_measure_value_rather_than_zero():
    from tests.conftest import baseline_of_record

    result = next(r for r in baseline_of_record().results if r.metric_id == "integrity").model_copy(deep=True)
    del result.details["exact_duplicates"]["tags"]
    result.details["dead_ends"] = None

    measures = {m.key: m.value for m in IntegrityMetric().measures(result)}

    assert measures["exact_duplicate_rows.tags"] is None
    assert measures["exact_duplicate_rows.categories"] is not None
    assert measures["dead_ends.categories_with_zero_specialities"] is None
