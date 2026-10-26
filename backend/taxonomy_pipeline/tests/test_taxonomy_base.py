from __future__ import annotations

import pytest

from taxonomy_pipeline.taxonomy.base import TagPath, TaxonomyDataset


@pytest.fixture
def dataset() -> TaxonomyDataset:
    return TaxonomyDataset(
        name="t",
        services=[{"id": 1, "name": "Industry"}, {"id": 2, "name": None}],
        categories=[
            {"id": 10, "name": "Banking", "service_id": 1},
            {"id": 11, "name": "Orphan", "service_id": 99},
        ],
        specialities=[
            {"id": 100, "name": "Retail", "category_id": 10},
            {"id": 101, "name": "Loose", "category_id": 98},
        ],
        tags=[{"id": 1000, "name": "Lending"}],
        speciality_tags=[
            {"speciality_id": 100, "tag_id": 1000},
            {"speciality_id": 404, "tag_id": 1000},
            {"speciality_id": 101, "tag_id": 1000},
            {"speciality_id": 100, "tag_id": 1000},
        ],
    )


def test_looks_up_each_rows_service_and_category_and_none_when_dangling(dataset: TaxonomyDataset) -> None:
    hierarchy = dataset.hierarchy()
    assert hierarchy.service_of(hierarchy.categories[10])["name"] == "Industry"
    assert hierarchy.service_of(hierarchy.categories[11]) is None
    assert hierarchy.category_of(hierarchy.specialities[100])["name"] == "Banking"
    assert hierarchy.category_of(hierarchy.specialities[101]) is None
    assert hierarchy.service_of(None) is None and hierarchy.category_of(None) is None


def test_a_service_name_is_none_for_an_unknown_service_or_an_unnamed_one(dataset: TaxonomyDataset) -> None:
    hierarchy = dataset.hierarchy()
    assert hierarchy.service_name(1) == "Industry"
    assert hierarchy.service_name(2) is None
    assert hierarchy.service_name(99) is None


def test_a_tags_parents_follow_its_links_in_order_including_repeats_and_dangling_ones(
    dataset: TaxonomyDataset,
) -> None:
    hierarchy = dataset.hierarchy()
    parents = hierarchy.parents("tags", {"id": 1000})
    assert [parent and parent["id"] for parent in parents] == [100, None, 101, 100]
    assert hierarchy.parents("categories", {"service_id": 1}) == [hierarchy.services[1]]
    assert hierarchy.parents("specialities", {"category_id": 98}) == [None]


def test_tag_paths_keep_one_path_per_distinct_existing_speciality(dataset: TaxonomyDataset) -> None:
    hierarchy = dataset.hierarchy()
    assert hierarchy.tag_paths(1000) == [
        TagPath(hierarchy.services[1], hierarchy.categories[10], hierarchy.specialities[100]),
        TagPath(None, None, hierarchy.specialities[101]),
    ]
    assert hierarchy.tag_paths(5555) == []


def test_a_repeated_id_resolves_to_its_last_row() -> None:
    dataset = TaxonomyDataset(name="t", services=[{"id": 1, "name": "First"}, {"id": 1, "name": "Second"}])
    assert dataset.hierarchy().service_name(1) == "Second"


def test_each_call_reflects_the_rows_as_they_are_now(dataset: TaxonomyDataset) -> None:
    dataset.services.append({"id": 3, "name": "Subject"})
    assert dataset.hierarchy().service_name(3) == "Subject"
