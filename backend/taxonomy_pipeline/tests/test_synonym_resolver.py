"""A term resolves to one concept, several (ambiguous) or none, and scoped synonyms only apply in their branch."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from taxonomy_pipeline.matching.names import normalize
from taxonomy_pipeline.synonyms import ConflictingSynonym, InvalidScope, Synonym, SynonymCreate, SynonymIndex
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

# Sales (1) > Sales Pitching (10) > tag 100 "Sales Pitch"
# Sports (2) > Football (20) > tags 200 "Pitch", 201 "Operational Risk"; Coaching (21) > tag 202 "operational risk"
TAXONOMY = TaxonomyDataset(
    name="original", services=[{"id": 1, "name": "Industry"}],
    categories=[{"id": 1, "name": "Sales", "service_id": 1}, {"id": 2, "name": "Sports", "service_id": 1}],
    specialities=[{"id": 10, "name": "Sales Pitching", "category_id": 1},
                  {"id": 20, "name": "Football", "category_id": 2},
                  {"id": 21, "name": "Football Coaching", "category_id": 2}],
    tags=[{"id": 100, "name": "Sales Pitch"}, {"id": 200, "name": "Pitch"}, {"id": 201, "name": "Operational Risk"},
          {"id": 202, "name": "operational risk"}],
    speciality_tags=[{"id": 1, "speciality_id": 10, "tag_id": 100}, {"id": 2, "speciality_id": 20, "tag_id": 200},
                     {"id": 3, "speciality_id": 20, "tag_id": 201}, {"id": 4, "speciality_id": 21, "tag_id": 202}],
)
NAMES = {("tags", "100"): "Sales Pitch", ("tags", "200"): "Pitch", ("tags", "201"): "Operational Risk",
         ("specialities", "20"): "Football", ("categories", "1"): "Sales"}


def synonym(term: str, level: str, concept_id: str, scope: tuple[str, str] | None = None,
            status: str = "approved") -> Synonym:
    return Synonym(
        synonym_id=f"{term}-{concept_id}-{scope}", term=term, normalized_term=normalize(term), dataset="original",
        concept_level=level, concept_id=concept_id, concept_name=NAMES[(level, concept_id)], snapshot_id="s",
        status=status, source="manual", reason="test", actor="test", created_at=datetime(2026, 11, 8, tzinfo=UTC),
        scope_level=scope[0] if scope else None, scope_id=scope[1] if scope else None,
    )


def request(term: str, level: str, concept_id: str, scope: tuple[str, str] | None = None) -> SynonymCreate:
    return SynonymCreate(dataset="original", concept_level=level, concept_id=concept_id, term=term, reason="test",
                         scope_level=scope[0] if scope else None, scope_id=scope[1] if scope else None)


def concepts(resolution) -> list[tuple[str, str, str]]:
    return [(m.concept_level, m.concept_id, m.via) for m in resolution.matches]


def test_a_concept_name_resolves_to_that_concept() -> None:
    resolution = SynonymIndex(TAXONOMY).resolve("  PITCH. ")
    assert resolution.status == "resolved" and concepts(resolution) == [("tags", "200", "name")]


def test_a_global_synonym_resolves_anywhere_and_is_flagged_as_a_synonym() -> None:
    index = SynonymIndex(TAXONOMY, [synonym("Pitch deck", "tags", "100")])
    for within in (None, ("categories", "2"), ("specialities", "10")):
        resolution = index.resolve("pitch deck", within=within)
        assert concepts(resolution) == [("tags", "100", "synonym")]
        assert resolution.matches[0].synonym_term == "Pitch deck"


def test_a_scoped_synonym_resolves_only_inside_its_branch() -> None:
    index = SynonymIndex(TAXONOMY, [synonym("Pitch", "tags", "100", ("categories", "1"))])
    assert concepts(index.resolve("pitch")) == [("tags", "200", "name")]
    assert concepts(index.resolve("pitch", within=("specialities", "20"))) == [("tags", "200", "name")]
    inside = index.resolve("pitch", within=("specialities", "10"))  # speciality 10 sits in category 1
    assert inside.status == "resolved" and concepts(inside) == [("tags", "100", "synonym")]
    assert inside.matches[0].scope_level == "categories"


def test_a_speciality_scope_is_more_specific_than_its_category_scope() -> None:
    index = SynonymIndex(TAXONOMY, [
        synonym("Turf", "tags", "201", ("categories", "2")), synonym("Turf", "tags", "200", ("specialities", "20")),
    ])
    assert concepts(index.resolve("turf", within=("specialities", "20"))) == [("tags", "200", "synonym")]
    assert concepts(index.resolve("turf", within=("specialities", "21"))) == [("tags", "201", "synonym")]


def test_a_term_matching_several_concepts_is_ambiguous_and_lists_them_all() -> None:
    resolution = SynonymIndex(TAXONOMY).resolve("operational risk")
    assert resolution.status == "ambiguous"
    assert concepts(resolution) == [("tags", "201", "name"), ("tags", "202", "name")]


def test_concept_names_win_over_global_synonyms() -> None:
    index = SynonymIndex(TAXONOMY, [synonym("Pitch", "tags", "100")])  # check_new would refuse this one
    assert concepts(index.resolve("pitch")) == [("tags", "200", "name")]


def test_unknown_terms_removed_synonyms_and_other_levels_do_not_resolve() -> None:
    index = SynonymIndex(TAXONOMY, [synonym("Soccer", "specialities", "20", status="removed"),
                                    synonym("Selling", "categories", "1")])
    assert index.resolve("cricket").status == "unknown"
    assert index.resolve("soccer").status == "unknown"
    assert index.resolve("selling", level="tags").status == "unknown"
    assert concepts(index.resolve("selling", level="categories")) == [("categories", "1", "synonym")]


def test_a_global_synonym_that_is_another_concepts_name_is_refused_unless_scoped() -> None:
    index = SynonymIndex(TAXONOMY)
    with pytest.raises(ConflictingSynonym, match="already the name of tag 200"):
        index.check_new(request("Pitch", "tags", "100"))
    index.check_new(request("Pitch", "tags", "100", ("categories", "1")))


def test_a_global_synonym_already_used_by_another_concept_is_refused() -> None:
    index = SynonymIndex(TAXONOMY, [synonym("Pitch deck", "tags", "100")])
    with pytest.raises(ConflictingSynonym, match="already a synonym of 'Sales Pitch'"):
        index.check_new(request("Pitch deck", "tags", "201"))
    index.check_new(request("Pitch deck", "tags", "201", ("specialities", "20")))


def test_two_concepts_cannot_share_a_synonym_in_the_same_scope() -> None:
    index = SynonymIndex(TAXONOMY, [synonym("Turf", "tags", "200", ("categories", "2"))])
    with pytest.raises(ConflictingSynonym, match="in that scope"):
        index.check_new(request("Turf", "tags", "201", ("categories", "2")))
    index.check_new(request("Turf", "tags", "201", ("specialities", "20")))


@pytest.mark.parametrize(("level", "concept_id", "scope", "message"), [
    ("tags", "100", ("categories", "2"), "doesn't sit inside category 2"),
    ("specialities", "20", ("specialities", "20"), "doesn't sit inside speciality 20"),
    ("categories", "1", ("categories", "1"), "doesn't sit inside category 1"),
    ("tags", "100", ("categories", "99"), "no category with ID 99"),
])
def test_a_scope_must_be_a_branch_the_concept_sits_in(
    level: str, concept_id: str, scope: tuple[str, str], message: str,
) -> None:
    with pytest.raises(InvalidScope, match=message):
        SynonymIndex(TAXONOMY).check_new(request("Anything", level, concept_id, scope))


def test_a_scope_needs_both_its_level_and_id() -> None:
    with pytest.raises(ValueError, match="both scope_level and scope_id"):
        SynonymCreate(dataset="original", concept_level="tags", concept_id="100", term="x", reason="r",
                      scope_level="categories")
