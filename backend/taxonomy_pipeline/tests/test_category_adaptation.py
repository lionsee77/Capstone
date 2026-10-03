import numpy as np

from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.metrics import category_adaptation
from taxonomy_pipeline.metrics.category_adaptation import CategoryAdaptationMetric


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


def test_matched_category_and_matched_speciality_resolves_both():
    # Baseline has "Retail Banking" (id 1) under the matched category, exactly
    # matching the source speciality tag 9001 hangs off - both levels resolve.
    result = _run([9001])

    placement = result.details["placements"][0]
    assert placement["speciality_action"] == "SKIP_DUPLICATE"
    assert placement["target_speciality_id"] == 1
    assert placement["target_speciality_name"] == "Retail Banking"


def test_matched_category_with_no_baseline_speciality_proposes_a_new_one():
    # Category "Banking" matches, but nothing under it matches "Astronaut
    # Training" - only the category matches this time (9002's own category
    # "Space Exploration" is unmatched in other tests; here we point it at
    # the matched "Banking" category instead to isolate the speciality check).
    datasets = _datasets()
    datasets["skillsfuture"].specialities[1]["category_id"] = 501  # under "Banking", not "Space Exploration"
    result = CategoryAdaptationMetric().run(
        datasets, {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9002]}
    )

    placement = result.details["placements"][0]
    assert placement["action"] == "SKIP_DUPLICATE"  # category still matches
    assert placement["speciality_action"] == "NEW_SPECIALITY"
    assert placement["target_speciality_id"] == "skillsfuture:602"
    assert placement["target_speciality_name"] == "Astronaut Training"


def test_new_category_cascades_to_new_speciality(monkeypatch):
    # A brand new category has no baseline specialities to compare against at
    # all, so its speciality is automatically proposed too, not left blank.
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _FakeSemanticMatcher)
    result = _run([9002], semantic_model="test-model", add_candidate_semantic_threshold=0.5)

    placement = result.details["placements"][0]
    assert placement["action"] == "NEW_CATEGORY"
    assert placement["speciality_action"] == "NEW_SPECIALITY"
    assert placement["target_speciality_id"] == "skillsfuture:602"


def test_review_ambiguous_category_skips_speciality_resolution(monkeypatch):
    # No point proposing or matching a speciality under a category that
    # isn't itself placed anywhere yet.
    class DisagreeingSemanticMatcher:
        def __init__(self, model_name):
            assert model_name == "test-model"

        def best_matches(self, queries, choices):
            return [(choices[0] if choices else None, 0.1) for _ in queries]

    monkeypatch.setattr(category_adaptation, "SemanticMatcher", DisagreeingSemanticMatcher)
    result = _run([9002], semantic_model="test-model", threshold=1)  # low threshold -> close_spelling tier reachable

    placement = result.details["placements"][0]
    assert placement["action"] == "REVIEW_AMBIGUOUS"
    assert "speciality_action" not in placement


def test_multiple_specialities_under_one_unresolved_category_collapse_to_one_row(monkeypatch):
    # Two specialities under the same still-unresolved category would
    # otherwise produce two byte-identical rows (the speciality never gets
    # looked at while the category itself is undecided) - collapse to one.
    class DisagreeingSemanticMatcher:
        def __init__(self, model_name):
            assert model_name == "test-model"

        def best_matches(self, queries, choices):
            return [(choices[0] if choices else None, 0.1) for _ in queries]

    datasets = _datasets()
    datasets["skillsfuture"].specialities.append({"id": 603, "name": "Zero Gravity Training", "category_id": 502})
    datasets["skillsfuture"].speciality_tags.append({"id": 703, "speciality_id": 603, "tag_id": 9002})
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", DisagreeingSemanticMatcher)
    result = CategoryAdaptationMetric().run(
        datasets,
        {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9002],
         "semantic_model": "test-model", "threshold": 1},
    )

    placements = result.details["placements"]
    assert len(placements) == 1
    assert placements[0]["action"] == "REVIEW_AMBIGUOUS"
    assert "source_speciality_id" not in placements[0]


def test_tag_with_specialities_under_two_categories_produces_two_placements():
    datasets = _datasets()
    datasets["skillsfuture"].speciality_tags.append({"id": 703, "speciality_id": 602, "tag_id": 9001})
    result = CategoryAdaptationMetric().run(
        datasets, {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9001]}
    )

    placements = result.details["placements"]
    assert len(placements) == 2
    assert {p["source_speciality_id"] for p in placements} == {601, 602}


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
    assert "speciality_action" not in placement  # category unresolved -> speciality never checked


class _InconsistentSemanticMatcher:
    """best_matches() (used to decide the category-level match) and
    similarity() (used separately by _uncovered_parents to recheck parent
    coverage) are two different calls on the real SemanticMatcher too -
    nothing guarantees they agree on every pair. This fake keeps
    best_matches confident so the category resolves, but similarity low, to
    deterministically exercise "category matched, but this speciality's own
    parent link looks uncovered" - a real embedding model can land here too.
    """

    def __init__(self, model_name):
        assert model_name == "test-model"

    def best_matches(self, queries, choices):
        return [(choices[0] if choices else None, 0.65) for _ in queries]

    def similarity(self, queries, choices):
        return np.zeros((len(queries), len(choices)))


def test_speciality_with_an_uncovered_parent_is_flagged_for_review_not_dropped(monkeypatch):
    # Same gap one level down, and one that's live today: SkillsFuture
    # specialities do have parent categories. speciality_outcomes.get(...)
    # used to return None here, so the record was reported as "no speciality
    # could be resolved" instead of being flagged for review.
    datasets = {
        "original": TaxonomyDataset(
            name="original",
            services=[{"id": 1, "name": "Industry"}],
            categories=[{"id": 1, "name": "Information Technology", "service_id": 1}],
            specialities=[{"id": 1, "name": "Infocomm Specialist", "category_id": 1}],
        ),
        "skillsfuture": TaxonomyDataset(
            name="skillsfuture",
            categories=[{"id": 501, "name": "Infocomm Tech", "service_id": 1}],
            specialities=[{"id": 601, "name": "Infocomm Specialist", "category_id": 501}],
            tags=[{"id": 9001, "name": "cloud computing"}],
            speciality_tags=[{"id": 701, "speciality_id": 601, "tag_id": 9001}],
        ),
    }
    monkeypatch.setattr(category_adaptation, "SemanticMatcher", _InconsistentSemanticMatcher)
    result = CategoryAdaptationMetric().run(
        datasets,
        {"baseline": "original", "incoming": "skillsfuture", "new_tag_ids": [9001], "semantic_model": "test-model"},
    )

    assert result.status == "ok"
    placement = result.details["placements"][0]
    assert placement["action"] in ("SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE")  # category itself resolved fine
    assert placement["speciality_action"] == "REVIEW_NEW_PLACEMENT"
    assert placement["needs_review"] is True
    assert placement["speciality_unmatched_incoming_parents"] == ["Infocomm Tech"]
    assert placement["speciality_existing_parents"] == ["Information Technology"]
