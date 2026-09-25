from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.metrics import category_adaptation
from taxonomy_pipeline.metrics.category_adaptation import CategoryAdaptationMetric


def _datasets() -> dict[str, TaxonomyDataset]:
    return {
        "original": TaxonomyDataset(
            name="original",
            categories=[{"id": 1, "name": "Banking", "service_id": 1}],
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


def test_unmatched_source_category_with_no_semantic_model_is_review_ambiguous():
    # Without a semantic model, a low-lexical-only score can never satisfy
    # saturation's "low_lexical AND low_semantic" rule for ADD_CANDIDATE
    # (semantic_score is None, never "< threshold"), so it stays uncertain
    # rather than being auto-promoted to a new category.
    result = _run([9002])

    placements = result.details["placements"]
    assert len(placements) == 1
    assert placements[0]["action"] == "REVIEW_AMBIGUOUS"
    assert placements[0]["needs_review"] is True


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
    assert by_tag[9002]["service_id"] == 1


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
