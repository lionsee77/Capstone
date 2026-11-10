import numpy as np

from taxonomy_pipeline.enrichment import category_adaptation
from taxonomy_pipeline.enrichment.category_adaptation import CategoryAdaptationMetric
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


def _datasets() -> dict[str, TaxonomyDataset]:
    return {
        "original": TaxonomyDataset(
            name="original",
            services=[{"id": 1, "name": "Industry"}],
            categories=[{"id": 1, "name": "Banking", "service_id": 1}],
            specialities=[{"id": 1, "name": "Retail Banking", "category_id": 1}],
        ),
        "skillsfuture": TaxonomyDataset(
            name="skillsfuture",
            categories=[
                {"id": 501, "name": "Banking", "service_id": 1},
                {"id": 502, "name": "Space Exploration", "service_id": 1},
            ],
            specialities=[
                {"id": 601, "name": "Retail Banking", "category_id": 501},
                {"id": 602, "name": "Astronaut Training", "category_id": 502},
            ],
            tags=[
                {"id": 9001, "name": "loan processing"},
                {"id": 9002, "name": "rocket fuel"},
                {"id": 9003, "name": "unlinked tag"},
            ],
            speciality_tags=[
                {"id": 701, "speciality_id": 601, "tag_id": 9001},
                {"id": 702, "speciality_id": 602, "tag_id": 9002},
            ],
        ),
    }


def _run(new_tag_ids, **params):
    return CategoryAdaptationMetric().run(
        _datasets(),
        {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": new_tag_ids, **params},
    )


class _FakeSemanticMatcher:
    """Forces "space exploration" to score as unrelated to anything in the
    baseline, so tests can reach ADD_CANDIDATE/NEW_CATEGORY without a real
    sentence-transformers model. Mirrors test_saturation.py's approach.
    """

    def __init__(self, model_name):
        assert model_name == "test-model"

    def best_matches(self, queries, choices):
        return [(choices[0] if choices else None, 0.1) for _ in queries]


def test_matched_source_category_reuses_the_existing_baseline_id():
    result = _run([9001])

    assert result.status == "ok"
    placements = result.details["placements"]
    assert len(placements) == 1
    placement = placements[0]
    assert placement["tag_id"] == 9001
    assert placement["source_category_name"] == "Banking"
    assert placement["action"] == "SKIP_DUPLICATE"
    assert placement["target_category_id"] == 1
    assert placement["needs_review"] is False
    assert placement["target_service_id"] == 1
    assert placement["target_service_name"] == "Industry"


def test_unmatched_tag_becomes_a_new_speciality_named_after_itself_under_the_matched_category():
    # "loan processing" matches no baseline speciality by content, so it falls
    # back to its source category ("Banking" -> baseline 1) and the tag itself
    # is the proposed speciality - never the source speciality it hangs off.
    result = _run([9001])

    placement = result.details["placements"][0]
    assert placement["placement_basis"] == "source_category"
    assert placement["speciality_action"] == "NEW_SPECIALITY"
    assert placement["target_speciality_id"] == "skillsfuture:tag:9001"
    assert placement["target_speciality_name"] == "loan processing"

def test_tag_matching_a_baseline_speciality_is_placed_there_regardless_of_source_category():
    # "Supplier Sourcing" is a Function-side skill; its source only knows it
    # through a sector. Content wins: it lands where the baseline already
    # keeps that speciality, with that category's real service.
    datasets = _datasets()
    datasets["original"].services.append({"id": 2, "name": "Function"})
    datasets["original"].categories.append({"id": 2, "name": "Procurement", "service_id": 2})
    datasets["original"].specialities.append({"id": 2, "name": "Supplier Sourcing", "category_id": 2})
    datasets["skillsfuture"].tags.append({"id": 9004, "name": "Supplier Sourcing"})
    datasets["skillsfuture"].speciality_tags.append({"id": 704, "speciality_id": 601, "tag_id": 9004})
    result = CategoryAdaptationMetric().run(
        datasets, {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9004]}
    )

    placements = result.details["placements"]
    assert len(placements) == 1
    placement = placements[0]
    assert placement["placement_basis"] == "content"
    assert placement["speciality_action"] == "SKIP_DUPLICATE"
    assert placement["target_speciality_id"] == 2
    assert placement["target_category_id"] == 2
    assert placement["target_category_name"] == "Procurement"
    assert placement["target_service_name"] == "Function"
    assert placement["needs_review"] is False
    assert "source_category_id" not in placement  # its sector was never needed

def test_new_category_cascades_to_new_speciality(monkeypatch):
    # A brand new category has no baseline specialities to compare against at
    # all, so the tag is proposed as its speciality too, not left blank.
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _FakeSemanticMatcher)
    result = _run([9002], semantic_model="test-model", add_candidate_semantic_threshold=0.5)

    placement = result.details["placements"][0]
    assert placement["action"] == "NEW_CATEGORY"
    assert placement["speciality_action"] == "NEW_SPECIALITY"
    assert placement["target_speciality_id"] == "skillsfuture:tag:9002"
    assert placement["target_speciality_name"] == "rocket fuel"

def test_review_ambiguous_category_offers_the_closest_categories_as_review_candidates(monkeypatch):
    # The category isn't confirmed, so nothing is decided automatically - but
    # the tag isn't dropped either: it is proposed under the closest baseline
    # categories, every row flagged for a reviewer to pick from.
    class DisagreeingSemanticMatcher:
        def __init__(self, model_name):
            assert model_name == "test-model"

        def best_matches(self, queries, choices):
            return [(choices[0] if choices else None, 0.1) for _ in queries]

        def similarity(self, queries, choices):
            return np.full((len(queries), len(choices)), 0.1)

    monkeypatch.setattr(category_adaptation, "SemanticMatcher", DisagreeingSemanticMatcher)
    result = _run([9002], semantic_model="test-model", threshold=1)  # low threshold -> close_spelling tier reachable

    placements = result.details["placements"]
    assert len(placements) == 1  # the baseline has a single category to offer
    placement = placements[0]
    assert placement["action"] == "REVIEW_AMBIGUOUS"
    assert placement["placement_basis"] == "review_candidate"
    assert placement["needs_review"] is True
    assert (placement["candidate_category_name"], placement["candidate_service_name"]) == ("Banking", "Industry")
    assert (placement["candidate_rank"], placement["candidate_count"]) == (1, 1)
    assert placement["target_speciality_name"] == "rocket fuel"
    assert placement["closest_speciality_name"] == "Retail Banking"


def test_review_candidates_are_the_three_closest_categories_with_a_known_service(monkeypatch):
    scores = {"aviation": 0.55, "logistics, shipping & aviation": 0.52, "travel": 0.41, "banking": 0.05,
              "orphan aviation": 0.99}

    class RankingSemanticMatcher:
        def __init__(self, model_name):
            assert model_name == "test-model"

        def best_matches(self, queries, choices):
            return [(choices[0] if choices else None, 0.55) for _ in queries]  # partial: REVIEW_AMBIGUOUS

        def similarity(self, queries, choices):
            return np.array([[scores.get(choice, 0.0) for choice in choices] for _ in queries])

    datasets = _datasets()
    datasets["original"].categories += [
        {"id": 2, "name": "Travel", "service_id": 1},
        {"id": 3, "name": "Logistics, Shipping & Aviation", "service_id": 1},
        {"id": 4, "name": "Aviation", "service_id": 1},
        {"id": 5, "name": "Orphan Aviation", "service_id": 99},  # no such service: can't be proposed
    ]
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", RankingSemanticMatcher)
    result = CategoryAdaptationMetric().run(datasets, {
        "baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9002], "semantic_model": "test-model",
    })

    placements = result.details["placements"]
    assert [p["candidate_category_name"] for p in placements] == [
        "Aviation", "Logistics, Shipping & Aviation", "Travel",
    ]
    assert [p["candidate_score"] for p in placements] == [0.55, 0.52, 0.41]
    assert {p["candidate_score_type"] for p in placements} == {"semantic"}
    assert all(p["needs_review"] and p["target_speciality_name"] == "rocket fuel" for p in placements)


def test_review_category_with_nothing_to_offer_stays_unresolved(monkeypatch):
    class DisagreeingSemanticMatcher:
        def __init__(self, model_name):
            assert model_name == "test-model"

        def best_matches(self, queries, choices):
            return [(choices[0] if choices else None, 0.1) for _ in queries]

        def similarity(self, queries, choices):
            return np.full((len(queries), len(choices)), 0.1)

    datasets = _datasets()
    datasets["original"].services = []  # no category has a service the contract could carry
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", DisagreeingSemanticMatcher)
    result = CategoryAdaptationMetric().run(datasets, {
        "baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9002], "semantic_model": "test-model",
        "threshold": 1,
    })

    placement = result.details["placements"][0]
    assert placement["action"] == "REVIEW_AMBIGUOUS"
    assert placement["placement_basis"] == "source_category"
    assert "candidate_category_name" not in placement


def test_content_match_without_a_known_service_falls_back_instead_of_aborting():
    # A matched speciality whose category has no service in the baseline can't be
    # written as a full contract record; it must not be taken as a content match.
    datasets = _datasets()
    datasets["original"].categories.append({"id": 2, "name": "Procurement", "service_id": 99})
    datasets["original"].specialities.append({"id": 2, "name": "Supplier Sourcing", "category_id": 2})
    datasets["skillsfuture"].tags.append({"id": 9004, "name": "Supplier Sourcing"})
    datasets["skillsfuture"].speciality_tags.append({"id": 704, "speciality_id": 601, "tag_id": 9004})
    result = CategoryAdaptationMetric().run(
        datasets, {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9004]}
    )

    [placement] = result.details["placements"]
    assert placement["placement_basis"] == "source_category"
    assert placement["target_category_name"] == "Banking"


def test_tag_linked_to_many_source_specialities_in_one_category_yields_one_placement():
    # SkillsFuture links one skill to up to ~100 job roles in a sector; they are
    # evidence for the sector, not placements of their own.
    datasets = _datasets()
    for offset, name in enumerate(["Bank Teller", "Branch Manager", "Credit Analyst", "Loan Officer"], start=3):
        datasets["skillsfuture"].specialities.append({"id": 600 + offset, "name": name, "category_id": 501})
        datasets["skillsfuture"].speciality_tags.append({"id": 700 + offset, "speciality_id": 600 + offset,
                                                         "tag_id": 9001})
    result = CategoryAdaptationMetric().run(
        datasets, {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9001]}
    )

    placements = result.details["placements"]
    assert len(placements) == 1
    assert placements[0]["source_speciality_count"] == 5
    assert placements[0]["source_speciality_sample"] == ["Bank Teller", "Branch Manager", "Credit Analyst"]


def test_source_speciality_names_never_become_speciality_names(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _FakeSemanticMatcher)
    result = _run([9001, 9002], semantic_model="test-model", add_candidate_semantic_threshold=0.5)

    proposed = {p.get("target_speciality_name") for p in result.details["placements"]}
    assert proposed == {"loan processing", "rocket fuel"}
    assert not proposed & {"Retail Banking", "Astronaut Training"}

def test_tag_under_two_source_categories_falls_back_to_one_placement_per_category():
    datasets = _datasets()
    datasets["skillsfuture"].speciality_tags.append({"id": 703, "speciality_id": 602, "tag_id": 9001})
    result = CategoryAdaptationMetric().run(
        datasets, {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9001]}
    )

    placements = result.details["placements"]
    assert len(placements) == 2
    assert {p["source_category_id"] for p in placements} == {501, 502}

def test_unmatched_source_category_becomes_new_category_even_without_semantic_model():
    # saturation.py's low_semantic check treats a missing semantic score (no
    # model configured) as "low" by default, so a low lexical score alone is
    # enough to reach ADD_CANDIDATE/NEW_CATEGORY - a semantic model narrows
    # false positives, it isn't required to reach this outcome at all.
    result = _run([9002])

    placements = result.details["placements"]
    assert len(placements) == 1
    assert placements[0]["action"] == "NEW_CATEGORY"
    assert placements[0]["needs_review"] is True


def test_close_lexical_spelling_alone_stays_ambiguous_not_a_match(monkeypatch):
    # The module's whole point (see saturation.py's docstring): close
    # spelling must be confirmed by meaning, or it doesn't count - a fake
    # matcher that disagrees keeps this REVIEW_AMBIGUOUS, never a skip.
    class DisagreeingSemanticMatcher:
        def __init__(self, model_name):
            assert model_name == "test-model"

        def best_matches(self, queries, choices):
            return [(choices[0] if choices else None, 0.1) for _ in queries]

    datasets = {
        "original": TaxonomyDataset(name="original", categories=[{"id": 1, "name": "Valuation", "service_id": 1}]),
        "skillsfuture": TaxonomyDataset(
            name="skillsfuture",
            categories=[{"id": 501, "name": "Evaluation", "service_id": 1}],
            specialities=[{"id": 601, "name": "Some Speciality", "category_id": 501}],
            tags=[{"id": 9001, "name": "some tag"}],
            speciality_tags=[{"id": 701, "speciality_id": 601, "tag_id": 9001}],
        ),
    }
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", DisagreeingSemanticMatcher)
    result = CategoryAdaptationMetric().run(
        datasets,
        {
            "baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9001],
            "semantic_model": "test-model", "threshold": 80,
        },
    )

    placements = result.details["placements"]
    assert len(placements) == 1
    assert placements[0]["action"] == "REVIEW_AMBIGUOUS"


def test_unmatched_source_category_becomes_a_new_category_with_a_namespaced_id(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _FakeSemanticMatcher)
    result = _run([9002], semantic_model="test-model", add_candidate_semantic_threshold=0.5)

    placements = result.details["placements"]
    assert len(placements) == 1
    placement = placements[0]
    assert placement["tag_id"] == 9002
    assert placement["action"] == "NEW_CATEGORY"
    assert placement["target_category_id"] == "skillsfuture:502"
    assert placement["needs_review"] is True


def test_new_category_service_id_defaults_from_matched_categories_in_the_same_run(monkeypatch):
    # Both tags in one run: 9001 matches baseline category 1 (service_id=1),
    # so 9002's brand new category should default to that same service.
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _FakeSemanticMatcher)
    result = _run([9001, 9002], semantic_model="test-model", add_candidate_semantic_threshold=0.5)

    by_tag = {p["tag_id"]: p for p in result.details["placements"]}
    assert by_tag[9002]["target_service_id"] == 1
    assert by_tag[9002]["target_service_name"] == "Industry"


def test_tag_with_no_speciality_link_is_flagged_not_placed():
    result = _run([9003])

    placements = result.details["placements"]
    assert placements == [
        {"tag_id": 9003, "tag_name": "unlinked tag", "action": "NO_SOURCE_CATEGORY", "needs_review": True}
    ]


def test_new_tag_ids_is_required():
    result = CategoryAdaptationMetric().run(
        _datasets(), {"baseline": "original", "incoming": "skillsfuture"}
    )
    assert result.status == "error"
    assert "new_tag_ids is required" in result.summary


def test_empty_new_tag_ids_is_not_an_error():
    result = _run([])
    assert result.status == "ok"
    assert result.details["placements"] == []


def test_unknown_tag_id_reports_an_error():
    result = _run([99999])
    assert result.status == "error"
    assert "99999" in result.summary


def test_category_with_a_mismatched_service_is_flagged_for_review_not_crashed():
    # The source category matches an existing baseline category by name, but
    # the source's own services table says it belongs to a different service
    # than the baseline records for that category - saturation's matcher
    # calls this REVIEW_NEW_PLACEMENT. _outcomes_by_incoming_id used to never
    # read that outcome list at all, so category_outcomes[category_id] raised
    # KeyError here and aborted the whole run. This only shows up once a
    # source actually has its own services table (SkillsFuture today doesn't,
    # which is why it was never caught before).
    datasets = _datasets()
    datasets["skillsfuture"].services = [{"id": 9, "name": "Totally Different Service"}]
    datasets["skillsfuture"].categories[0]["service_id"] = 9  # category 501 "Banking"
    result = CategoryAdaptationMetric().run(
        datasets, {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9001]}
    )

    assert result.status == "ok"
    placement = result.details["placements"][0]
    assert placement["action"] == "REVIEW_NEW_PLACEMENT"
    assert placement["needs_review"] is True
    assert placement["unmatched_incoming_parents"] == ["Totally Different Service"]
    assert placement["existing_parents"] == ["Industry"]
    # Not confirmed, so only offered for review - never placed automatically.
    assert placement["placement_basis"] == "review_candidate"
    assert placement["candidate_category_name"] == "Banking"
    assert placement["candidate_score_type"] == "lexical"


# --- #132: opt-in placement options -----------------------------------------


class _ScopedMatcher:
    """Unrelated to everything by default, but scores a tag and a speciality that
    share a word as `shared_word_score`, so a test controls whether the
    category-scoped search finds a home."""

    shared_word_score = 0.7

    def __init__(self, model_name):
        assert model_name == "test-model"

    def best_matches(self, queries, choices):
        return [(choices[0] if choices else None, 0.1) for _ in queries]

    def top_matches(self, queries, choices, k):
        return [[(choice, 0.1) for choice in choices[:k]] for _ in queries]

    def similarity(self, queries, choices):
        return np.array([
            [self.shared_word_score if set(query.split()) & set(choice.split()) else 0.1 for choice in choices]
            for query in queries
        ])


def _two_sector_datasets() -> dict[str, TaxonomyDataset]:
    """'loan processing' sits in two SkillsFuture sectors: Banking (one job role)
    and Insurance (two job roles); both sectors match baseline categories."""
    datasets = _datasets()
    datasets["original"].categories.append({"id": 2, "name": "Insurance", "service_id": 1})
    datasets["original"].specialities.append({"id": 2, "name": "Claims Handling", "category_id": 2})
    datasets["skillsfuture"].categories.append({"id": 503, "name": "Insurance", "service_id": 1})
    datasets["skillsfuture"].specialities += [
        {"id": 603, "name": "Claims Officer", "category_id": 503},
        {"id": 604, "name": "Underwriter", "category_id": 503},
    ]
    datasets["skillsfuture"].speciality_tags += [
        {"id": 703, "speciality_id": 603, "tag_id": 9001},
        {"id": 704, "speciality_id": 604, "tag_id": 9001},
    ]
    return datasets


def _run_on(datasets, new_tag_ids, **params):
    return CategoryAdaptationMetric().run(
        datasets, {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": new_tag_ids, **params},
    )


def test_a_tag_in_several_sectors_gets_one_placement_per_sector_by_default():
    placements = _run_on(_two_sector_datasets(), [9001]).details["placements"]

    assert sorted(placement["target_category_name"] for placement in placements) == ["Banking", "Insurance"]
    assert all("other_source_categories" not in placement for placement in placements)


def test_one_category_per_tag_keeps_the_sector_with_the_most_linking_job_roles():
    placements = _run_on(_two_sector_datasets(), [9001], one_category_per_tag=True).details["placements"]

    assert len(placements) == 1
    assert placements[0]["target_category_name"] == "Insurance"  # two job roles beat one
    assert placements[0]["other_source_categories"] == ["Banking"]


def test_one_category_per_tag_prefers_a_matched_category_over_a_new_one(monkeypatch):
    # "rocket fuel" also sits in Banking; Space Exploration would be a new
    # category, so the matched one wins even with equal evidence.
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _FakeSemanticMatcher)
    datasets = _datasets()
    datasets["skillsfuture"].speciality_tags.append({"id": 705, "speciality_id": 601, "tag_id": 9002})

    placements = _run_on(
        datasets, [9002], semantic_model="test-model", one_category_per_tag=True,
    ).details["placements"]

    assert [placement["action"] for placement in placements] == ["SKIP_DUPLICATE"]
    assert placements[0]["other_source_categories"] == ["Space Exploration"]


def test_category_scoped_search_joins_an_existing_speciality_in_the_matched_category(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    datasets = _datasets()
    datasets["original"].specialities.append({"id": 3, "name": "Loan Servicing", "category_id": 1})

    placement = _run_on(
        datasets, [9001], semantic_model="test-model", category_scoped_semantic_threshold=0.55,
    ).details["placements"][0]

    assert placement["placement_basis"] == "category_content"
    assert placement["speciality_action"] == "SKIP_CATEGORY_SCOPED_MATCH"
    assert placement["target_speciality_id"] == 3
    assert placement["target_speciality_name"] == "Loan Servicing"
    assert placement["speciality_semantic_score"] == 0.7
    assert placement["needs_review"] is False


def test_category_scoped_search_below_its_threshold_still_proposes_a_new_speciality(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    datasets = _datasets()
    datasets["original"].specialities.append({"id": 3, "name": "Loan Servicing", "category_id": 1})

    placement = _run_on(
        datasets, [9001], semantic_model="test-model", category_scoped_semantic_threshold=0.75,
    ).details["placements"][0]

    assert placement["placement_basis"] == "source_category"
    assert placement["speciality_action"] == "NEW_SPECIALITY"
    assert placement["target_speciality_name"] == "loan processing"


def test_category_scoped_search_never_looks_outside_the_matched_category(monkeypatch):
    # "Loan Servicing" would match, but it sits under Insurance, not Banking.
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    datasets = _datasets()
    datasets["original"].categories.append({"id": 2, "name": "Insurance", "service_id": 1})
    datasets["original"].specialities.append({"id": 3, "name": "Loan Servicing", "category_id": 2})

    placement = _run_on(
        datasets, [9001], semantic_model="test-model", category_scoped_semantic_threshold=0.55,
    ).details["placements"][0]

    assert placement["speciality_action"] == "NEW_SPECIALITY"


def test_category_scoped_search_needs_a_semantic_model():
    result = _run([9001], category_scoped_semantic_threshold=0.55)

    assert result.status == "error"
    assert "needs a semantic_model" in result.summary


def test_one_category_per_tag_must_be_a_boolean():
    result = _run([9001], one_category_per_tag="yes")

    assert result.status == "error"
    assert "one_category_per_tag" in result.summary


def _banking_tags_datasets(tag_names: dict[int, str]) -> dict[str, TaxonomyDataset]:
    """Every tag in `tag_names` sits only in SkillsFuture's Banking sector, which
    matches the baseline's Banking category, and matches no existing speciality."""
    datasets = _datasets()
    incoming = datasets["skillsfuture"]
    incoming.tags = [{"id": tag_id, "name": name} for tag_id, name in tag_names.items()]
    incoming.speciality_tags = [
        {"id": 800 + index, "speciality_id": 601, "tag_id": tag_id} for index, tag_id in enumerate(tag_names)
    ]
    return datasets


def _speciality_by_tag(result) -> dict[str, str]:
    return {placement["tag_name"]: placement["target_speciality_name"] for placement in result.details["placements"]}


def test_groups_new_specialities_close_in_meaning_under_the_most_central_tag(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    datasets = _banking_tags_datasets({9001: "loan processing", 9004: "loan approval", 9005: "mortgage advice"})

    result = _run_on(datasets, [9001, 9004, 9005], semantic_model="test-model",
                     group_new_specialities_threshold=0.6)

    # Both loan tags have one neighbour, so the tie goes to the name sorted first.
    assert _speciality_by_tag(result) == {
        "loan processing": "loan approval", "loan approval": "loan approval", "mortgage advice": "mortgage advice",
    }
    member = next(p for p in result.details["placements"] if p["tag_name"] == "loan processing")
    assert member["speciality_group_size"] == 2 and member["speciality_group_leader"] is False
    assert member["speciality_group_score"] == 0.7
    assert member["target_speciality_id"] == "skillsfuture:tag:9004"
    assert "1 new-speciality groups" in result.summary


def test_grouping_never_chains_tags_that_are_only_close_through_another_tag(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    datasets = _banking_tags_datasets({
        9001: "alpha beta", 9004: "beta gamma", 9005: "gamma delta", 9006: "delta epsilon",
    })

    result = _run_on(datasets, [9001, 9004, 9005, 9006], semantic_model="test-model",
                     group_new_specialities_threshold=0.6)

    # "delta epsilon" is close to "gamma delta" but not to the leader, so it stays apart.
    assert _speciality_by_tag(result) == {
        "alpha beta": "beta gamma", "beta gamma": "beta gamma", "gamma delta": "beta gamma",
        "delta epsilon": "delta epsilon",
    }


def test_leaves_new_specialities_apart_below_the_grouping_threshold(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    datasets = _banking_tags_datasets({9001: "loan processing", 9004: "loan approval"})

    result = _run_on(datasets, [9001, 9004], semantic_model="test-model", group_new_specialities_threshold=0.75)

    assert _speciality_by_tag(result) == {"loan processing": "loan processing", "loan approval": "loan approval"}


def test_never_groups_tags_proposed_under_different_categories(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    datasets = _banking_tags_datasets({9001: "loan processing", 9004: "loan approval"})
    datasets["original"].categories.append({"id": 2, "name": "Insurance", "service_id": 1})
    datasets["skillsfuture"].categories.append({"id": 503, "name": "Insurance", "service_id": 1})
    datasets["skillsfuture"].specialities.append({"id": 603, "name": "Claims Officer", "category_id": 503})
    datasets["skillsfuture"].speciality_tags[1]["speciality_id"] = 603  # loan approval -> Insurance only

    result = _run_on(datasets, [9001, 9004], semantic_model="test-model", group_new_specialities_threshold=0.6)

    assert _speciality_by_tag(result) == {"loan processing": "loan processing", "loan approval": "loan approval"}


def test_grouping_needs_a_semantic_model():
    result = _run([9001], group_new_specialities_threshold=0.6)

    assert result.status == "error"
    assert "group_new_specialities_threshold needs a semantic_model" in result.summary


def test_flags_only_the_new_specialities_left_holding_a_single_tag(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    datasets = _banking_tags_datasets({9001: "loan processing", 9004: "loan approval", 9005: "mortgage advice"})

    result = _run_on(datasets, [9001, 9004, 9005], semantic_model="test-model",
                     group_new_specialities_threshold=0.6, flag_single_tag_specialities=True)

    flagged = {p["tag_name"]: p["needs_review"] for p in result.details["placements"]}
    assert flagged == {"loan processing": False, "loan approval": False, "mortgage advice": True}
    assert "1 single-tag placements flagged" in result.summary


def test_flagging_single_tag_specialities_leaves_matches_to_existing_ones_alone(monkeypatch):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    datasets = _banking_tags_datasets({9001: "loan processing"})
    datasets["original"].specialities.append({"id": 3, "name": "Loan Servicing", "category_id": 1})

    placement = _run_on(
        datasets, [9001], semantic_model="test-model", category_scoped_semantic_threshold=0.55,
        flag_single_tag_specialities=True,
    ).details["placements"][0]

    assert placement["speciality_action"] == "SKIP_CATEGORY_SCOPED_MATCH"
    assert placement["needs_review"] is False and "single_tag_speciality" not in placement


def test_flag_single_tag_specialities_must_be_a_boolean():
    result = _run([9001], flag_single_tag_specialities="yes")

    assert result.status == "error"
    assert "flag_single_tag_specialities" in result.summary


def test_groups_review_candidates_only_on_each_tags_first_candidate_category():
    def candidate(tag_id, name, category_id, rank):
        return {"tag_id": tag_id, "tag_name": name, "placement_basis": "review_candidate",
                "speciality_action": "NEW_SPECIALITY", "candidate_category_id": category_id, "candidate_rank": rank,
                "candidate_category_name": f"Category {category_id}",
                "target_speciality_id": f"skillsfuture:tag:{tag_id}", "target_speciality_name": name}

    placements = [
        candidate(9001, "loan processing", 1, 1), candidate(9001, "loan processing", 2, 2),
        candidate(9004, "loan approval", 1, 1), candidate(9004, "loan approval", 2, 2),
    ]

    groups = category_adaptation._group_new_specialities(
        placements, _ScopedMatcher("test-model"), 0.6, "skillsfuture",
    )

    assert groups == 1
    by_rank = {(p["tag_name"], p["candidate_rank"]): p["target_speciality_name"] for p in placements}
    assert by_rank == {
        ("loan processing", 1): "loan approval", ("loan approval", 1): "loan approval",
        ("loan processing", 2): "loan processing", ("loan approval", 2): "loan approval",
    }


class _FakeNamer:
    """Names each group from `names` in order; records what it was asked."""

    instances: list["_FakeNamer"] = []
    names: list[str | None] = []

    def __init__(self, model, *, cache_path=None):
        self.model, self.cache_path, self.groups = model, cache_path, []
        _FakeNamer.instances.append(self)

    def name(self, groups):
        self.groups.extend(groups)
        return list(_FakeNamer.names[: len(groups)])


def _named_run(monkeypatch, names, tag_names, **params):
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _ScopedMatcher)
    monkeypatch.setattr(category_adaptation, "LLMGroupNamer", _FakeNamer)
    _FakeNamer.instances, _FakeNamer.names = [], names
    return _run_on(_banking_tags_datasets(tag_names), list(tag_names), semantic_model="test-model",
                   group_new_specialities_threshold=0.6, group_name_model="gpt-test", **params)


def test_names_a_group_from_all_its_tags_and_holds_the_name_for_review(monkeypatch):
    result = _named_run(monkeypatch, ["Loan Origination"], {9001: "loan processing", 9004: "loan approval"},
                        group_name_cache="names.json")

    namer, = _FakeNamer.instances
    assert (namer.model, namer.cache_path) == ("gpt-test", "names.json")
    assert [(g.category, g.tags) for g in namer.groups] == [("Banking", ("loan approval", "loan processing"))]
    for placement in result.details["placements"]:
        assert placement["target_speciality_name"] == "Loan Origination"
        assert placement["speciality_name_source"] == "generated" and placement["needs_review"] is True
    assert "(1 named by the model)" in result.summary


def test_falls_back_to_the_central_tag_when_the_generated_name_already_exists(monkeypatch):
    result = _named_run(monkeypatch, ["retail banking"], {9001: "loan processing", 9004: "loan approval"})

    placement = result.details["placements"][0]
    assert placement["target_speciality_name"] == "loan approval"
    assert placement["speciality_name_source"] == "central_tag"
    assert placement["speciality_name_fallback"] == "the generated name 'retail banking' already exists"
    assert placement["needs_review"] is False


def test_falls_back_when_the_generated_name_repeats_another_new_speciality(monkeypatch):
    # "mortgage advice" stays a one-tag speciality named after itself; the group can't take that name.
    result = _named_run(monkeypatch, ["Mortgage Advice"],
                        {9001: "loan processing", 9004: "loan approval", 9005: "mortgage advice"})

    assert _speciality_by_tag(result)["loan processing"] == "loan approval"


def test_falls_back_when_no_usable_name_came_back(monkeypatch):
    result = _named_run(monkeypatch, [None], {9001: "loan processing", 9004: "loan approval"})

    placement = result.details["placements"][0]
    assert placement["target_speciality_name"] == "loan approval"
    assert placement["speciality_name_fallback"] == "no usable name came back"


def test_naming_groups_needs_grouping_on():
    result = _run([9001], group_name_model="gpt-test")

    assert result.status == "error"
    assert "set group_new_specialities_threshold too" in result.summary
