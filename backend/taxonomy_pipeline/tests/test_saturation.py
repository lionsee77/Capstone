import sys
import types

import numpy as np
import pytest

from taxonomy_pipeline.matching import embeddings
from taxonomy_pipeline.quality import saturation
from taxonomy_pipeline.quality.saturation import SaturationMetric
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


def _datasets() -> dict[str, TaxonomyDataset]:
    return {
        "original": TaxonomyDataset(
            name="original",
            categories=[{"id": 1, "name": "Financial Services"}],
            specialities=[{"id": 10, "name": "Payroll Processing"}],
            tags=[{"id": 100, "name": "accounting"}],
        ),
        "skillsfuture": TaxonomyDataset(
            name="skillsfuture",
            categories=[
                {"id": 1, "name": "Financial Services"},
                {"id": 2, "name": "Marine Services"},
            ],
            specialities=[
                {"id": 10, "name": "Payroll Process"},
                {"id": 11, "name": "Carbon Accounting"},
            ],
            tags=[
                {"id": 100, "name": "Accounting"},
                {"id": 101, "name": "Carbon Reporting"},
                {"id": 102, "name": "Carbon Reporting"},
            ],
        ),
    }


def _ambiguous(level_result, name):
    return next(c for c in level_result["ambiguous_concepts"] if c["incoming_name"] == name)


def test_compares_incoming_taxonomy_and_reports_ambiguous_concepts():
    result = SaturationMetric().run(
        _datasets(), {"baseline": "original", "incoming": "skillsfuture", "threshold": 90}
    )

    assert result.status == "ok"
    overall = result.details["overall"]
    # Six distinct incoming concepts: 2 categories, 2 specialities, 2 tags.
    # Payroll Process is a close spelling with no meaning model to confirm it,
    # so it goes to review instead of being skipped.
    assert overall == {"relevant_concepts_reviewed": 6, "ambiguous": 4, "ambiguous_rate": 0.667}

    levels = result.details["by_concept_level"]
    assert levels["categories"]["exact_matches"] == 1
    assert levels["categories"]["exact_match_details"][0]["recommended_action"] == "SKIP_DUPLICATE"
    assert levels["specialities"]["fuzzy_candidate_matches"] == 0
    payroll = _ambiguous(levels["specialities"], "Payroll Process")
    assert payroll["recommended_action"] == "REVIEW_AMBIGUOUS"
    assert payroll["reason"] == "close_spelling_unconfirmed"
    assert [concept["incoming_name"] for concept in levels["tags"]["ambiguous_concepts"]] == [
        "Carbon Reporting"
    ]
    # Without a meaning model, a low spelling score alone is enough for ADD_CANDIDATE.
    assert levels["tags"]["ambiguous_concepts"][0]["recommended_action"] == "ADD_CANDIDATE"


def test_missing_datasets_report_an_error():
    result = SaturationMetric().run(
        _datasets(), {"baseline": "original", "incoming": "not-loaded"}
    )
    assert result.status == "error"


def test_invalid_threshold_reports_an_error():
    result = SaturationMetric().run(
        _datasets(), {"baseline": "original", "incoming": "skillsfuture", "threshold": 101}
    )
    assert result.status == "error"


def test_semantic_candidates_are_reported_separately_from_lexical_matches(monkeypatch):
    class FakeSemanticMatcher:
        def __init__(self, model_name, revision=None):
            assert model_name == "test-model"

        def best_matches(self, queries, choices):
            assert choices
            results = {
                "marine services": ("financial services", 0.20),
                "payroll process": ("payroll processing", 0.91),
                "carbon accounting": ("payroll processing", 0.50),
                "carbon reporting": ("accounting", 0.81),
            }
            # Exact matches are sent to the matcher too; they have no entry here.
            return [results.get(query, (None, None)) for query in queries]

    monkeypatch.setattr(saturation, "SemanticMatcher", FakeSemanticMatcher)
    result = SaturationMetric().run(
        _datasets(),
        {
            "baseline": "original",
            "incoming": "skillsfuture",
            "threshold": 95,
            "semantic_model": "test-model",
            "semantic_threshold": 0.8,
            "add_candidate_lexical_threshold": 70,
        },
    )

    assert result.status == "ok"
    overall = result.details["overall"]
    # The two semantic candidates are represented in the review result;
    # Marine Services and Carbon Accounting remain ambiguous.
    assert overall == {"relevant_concepts_reviewed": 6, "ambiguous": 2, "ambiguous_rate": 0.333}
    levels = result.details["by_concept_level"]
    assert levels["specialities"]["semantic_candidate_matches"] == 1
    assert levels["specialities"]["semantic_match_details"][0]["recommended_action"] == "SKIP_SEMANTIC_DUPLICATE"
    assert levels["tags"]["semantic_match_details"][0]["incoming_name"] == "Carbon Reporting"
    assert levels["categories"]["ambiguous_concepts"][0]["recommended_action"] == "ADD_CANDIDATE"
    assert levels["specialities"]["ambiguous_concepts"][0]["recommended_action"] == "REVIEW_AMBIGUOUS"


def test_a_pinned_model_revision_is_loaded_and_recorded(monkeypatch):
    loaded = []

    class RecordingSemanticMatcher:
        def __init__(self, model_name, revision=None):
            loaded.append((model_name, revision))

        def best_matches(self, queries, choices):
            return [(None, None) for _ in queries]

        def similarity(self, queries, choices):
            return np.zeros((len(queries), len(choices)))

    monkeypatch.setattr(saturation, "SemanticMatcher", RecordingSemanticMatcher)
    result = SaturationMetric().run(_datasets(), {
        "baseline": "original", "incoming": "skillsfuture",
        "semantic_model": "test-model", "semantic_model_revision": "abc123",
    })

    assert result.status == "ok"
    assert loaded == [("test-model", "abc123")]
    assert result.details["semantic_model_revision"] == "abc123"


@pytest.mark.parametrize("model", ["all-MiniLM-L6-v2", "sentence-transformers/all-MiniLM-L6-v2"])
def test_an_unpinned_config_loads_the_model_at_the_revision_pinned_in_code(monkeypatch, model):
    loaded = []

    class RecordingSemanticMatcher:
        def __init__(self, model_name, revision=None):
            loaded.append((model_name, revision))

        def best_matches(self, queries, choices):
            return [(None, None) for _ in queries]

        def similarity(self, queries, choices):
            return np.zeros((len(queries), len(choices)))

    monkeypatch.setattr(saturation, "SemanticMatcher", RecordingSemanticMatcher)
    result = SaturationMetric().run(_datasets(), {
        "baseline": "original", "incoming": "skillsfuture", "semantic_model": model,
    })

    assert loaded == [(model, embeddings.MODEL_REVISION)]
    assert result.details["semantic_model_revision"] == embeddings.MODEL_REVISION


def test_the_shared_matcher_defaults_to_the_pinned_revision_for_case_a_and_merge_preview(monkeypatch):
    loaded = []

    class FakeSentenceTransformer:
        def __init__(self, model_name, revision=None):
            loaded.append((model_name, revision))

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(
        SentenceTransformer=FakeSentenceTransformer,
    ))
    saturation.SemanticMatcher("all-MiniLM-L6-v2")
    saturation.SemanticMatcher("some-other-model")

    assert loaded == [("all-MiniLM-L6-v2", embeddings.MODEL_REVISION), ("some-other-model", None)]


@pytest.mark.parametrize(("params", "message"), [
    ({"semantic_model_revision": "abc123"}, "semantic_model_revision needs semantic_model"),
    ({"semantic_model": "test-model", "semantic_model_revision": " "}, "must be a model commit"),
])
def test_a_model_revision_must_pin_a_configured_model(params, message):
    result = SaturationMetric().run(_datasets(), {"baseline": "original", "incoming": "skillsfuture", **params})

    assert result.status == "error"
    assert message in result.summary


def test_invalid_semantic_threshold_reports_an_error():
    result = SaturationMetric().run(
        _datasets(),
        {"baseline": "original", "incoming": "skillsfuture", "semantic_threshold": 1.1},
    )
    assert result.status == "error"


def test_empty_incoming_taxonomy_reports_an_error():
    datasets = _datasets()
    datasets["skillsfuture"] = TaxonomyDataset(name="skillsfuture")

    result = SaturationMetric().run(datasets, {"baseline": "original", "incoming": "skillsfuture"})

    assert result.status == "error"
    assert "no named categories" in result.summary


def test_empty_baseline_level_with_incoming_records_reports_an_error():
    datasets = _datasets()
    datasets["original"] = TaxonomyDataset(
        name="original",
        specialities=[{"id": 10, "name": "Payroll Processing"}],
        tags=[{"id": 100, "name": "accounting"}],
    )

    result = SaturationMetric().run(datasets, {"baseline": "original", "incoming": "skillsfuture"})

    assert result.status == "error"
    assert "categories" in result.summary
    assert "table_map" in result.summary


def test_batches_lexical_comparisons_with_cdist(monkeypatch):
    original_cdist = saturation.process.cdist
    calls = []

    def recording_cdist(*args, **kwargs):
        calls.append((args, kwargs))
        return original_cdist(*args, **kwargs)

    monkeypatch.setattr(saturation.process, "cdist", recording_cdist)
    result = SaturationMetric().run(
        _datasets(), {"baseline": "original", "incoming": "skillsfuture", "threshold": 90}
    )

    assert result.status == "ok"
    # One batch per level for the same level, one for the other levels.
    assert len(calls) == 6


def test_invalid_add_candidate_threshold_reports_an_error():
    result = SaturationMetric().run(
        _datasets(),
        {"baseline": "original", "incoming": "skillsfuture", "add_candidate_lexical_threshold": 101},
    )

    assert result.status == "error"


def _fake_matcher(monkeypatch, results, parent_similarity=None):
    parent_similarity = parent_similarity or {}
    class FakeSemanticMatcher:
        def __init__(self, model_name, revision=None):
            pass

        def best_matches(self, queries, choices):
            # results: query -> (name, score) or a list of them; the first
            # name present in `choices` is returned, as a real model would.
            answers = []
            for query in queries:
                options = results.get(query, [])
                options = [options] if isinstance(options, tuple) else options
                answers.append(next(((n, sc) for n, sc in options if n in choices), (None, None)))
            return answers

        def similarity(self, queries, choices):
            # parent_similarity: {(query, choice): score}; anything else is 0.
            return np.array([[parent_similarity.get((q, c), 0.0) for c in choices] for q in queries])

    monkeypatch.setattr(saturation, "SemanticMatcher", FakeSemanticMatcher)


def _run(datasets, **params):
    return SaturationMetric().run(datasets, {"baseline": "original", "incoming": "skillsfuture", **params})


def _pair(baseline: dict, incoming: dict) -> dict[str, TaxonomyDataset]:
    return {
        "original": TaxonomyDataset(name="original", **baseline),
        "skillsfuture": TaxonomyDataset(name="skillsfuture", **incoming),
    }


_BASE = {
    "categories": [{"id": 1, "name": "Finance", "status": "1"}],
    "specialities": [{"id": 10, "name": "Valuation", "category_id": 1}],
    "tags": [{"id": 100, "name": "budgeting"}],
}


def test_add_candidate_without_semantic_model():
    datasets = _pair(_BASE, {"tags": [{"id": 1, "name": "Marine Welding"}]})
    tags = _run(datasets).details["by_concept_level"]["tags"]
    assert tags["recommended_action_counts"]["ADD_CANDIDATE"] == 1
    assert tags["new_rate_min"] == 1.0


def test_close_spelling_needs_semantic_agreement(monkeypatch):
    datasets = _pair(_BASE, {"specialities": [{"id": 1, "name": "Evaluation"}]})
    level = _run(datasets).details["by_concept_level"]["specialities"]
    assert level["fuzzy_candidate_matches"] == 0
    assert _ambiguous(level, "Evaluation")["reason"] == "close_spelling_unconfirmed"

    _fake_matcher(monkeypatch, {"evaluation": ("valuation", 0.4)})
    level = _run(datasets, semantic_model="m").details["by_concept_level"]["specialities"]
    assert _ambiguous(level, "Evaluation")["recommended_action"] == "REVIEW_AMBIGUOUS"

    _fake_matcher(monkeypatch, {"evaluation": ("valuation", 0.9)})
    level = _run(datasets, semantic_model="m").details["by_concept_level"]["specialities"]
    assert level["fuzzy_match_details"][0]["baseline_name"] == "Valuation"
    assert level["fuzzy_match_details"][0]["recommended_action"] == "SKIP_DUPLICATE"


def test_ampersand_and_plural_fold_to_exact_matches():
    baseline = {"categories": [{"id": 1, "name": "Energy and Chemicals"}], "tags": [{"id": 1, "name": "assessment"}]}
    datasets = _pair(
        baseline, {"categories": [{"id": 5, "name": "Energy & Chemicals"}], "tags": [{"id": 7, "name": "Assessments"}]}
    )
    levels = _run(datasets).details["by_concept_level"]
    assert levels["categories"]["exact_matches"] == 1
    assert levels["tags"]["exact_matches"] == 1


def test_concept_under_two_parents_counts_once_and_lists_both_parents():
    incoming = {
        "categories": [{"id": 1, "name": "Finance"}, {"id": 2, "name": "Health"}],
        "specialities": [
            {"id": 10, "name": "Machine Learning", "category_id": 1},
            {"id": 11, "name": "machine learning", "category_id": 2},
        ],
    }
    level = _run(_pair(_BASE, incoming)).details["by_concept_level"]["specialities"]
    assert level["relevant_concepts_reviewed"] == 1
    concept = _ambiguous(level, "Machine Learning")
    assert concept["incoming_ids"] == [10, 11]
    assert concept["parent_status"] == "HAS_PARENT"
    assert concept["parent_level"] == "categories"
    assert concept["parents"] == [{"id": 1, "name": "Finance"}, {"id": 2, "name": "Health"}]


def test_parent_status_is_resolved_inside_the_incoming_source():
    incoming = {
        "categories": [{"id": 1, "name": "Marine", "service_id": 1}],
        "specialities": [
            {"id": 10, "name": "Ship Welder", "category_id": 1},
            {"id": 11, "name": "Deck Cadet", "category_id": 99},
        ],
        "tags": [{"id": 100, "name": "Hull Inspection"}, {"id": 101, "name": "Knot Tying"}],
        "speciality_tags": [{"id": 1, "speciality_id": 10, "tag_id": 100}],
    }
    levels = _run(_pair(_BASE, incoming)).details["by_concept_level"]
    # The incoming source defines no services, so its categories have no parent.
    assert _ambiguous(levels["categories"], "Marine")["parent_status"] == "NO_PARENT"
    assert _ambiguous(levels["specialities"], "Ship Welder")["parents"] == [{"id": 1, "name": "Marine"}]
    assert _ambiguous(levels["specialities"], "Deck Cadet")["parent_status"] == "NO_PARENT"
    hull = _ambiguous(levels["tags"], "Hull Inspection")
    assert (hull["parent_status"], hull["parent_count"]) == ("HAS_PARENT", 1)
    assert _ambiguous(levels["tags"], "Knot Tying")["parent_status"] == "NO_PARENT"
    assert levels["specialities"]["parent_status_counts"] == {"HAS_PARENT": 1, "NO_PARENT": 1}
    assert levels["specialities"]["parent_status_counts_flagged"] == {"HAS_PARENT": 1, "NO_PARENT": 1}


def test_concept_known_at_another_level_is_a_duplicate():
    datasets = _pair(_BASE, {"tags": [{"id": 5, "name": "Valuation"}]})
    tags = _run(datasets).details["by_concept_level"]["tags"]
    match = tags["exact_match_details"][0]
    assert (match["baseline_level"], match["recommended_action"]) == ("specialities", "SKIP_DUPLICATE")
    assert match["parent_check"] == "not_checked_for_tags"
    assert tags["other_level_matches"] == 1
    assert tags["new_rate_max"] == 0.0


def test_placeholder_and_blank_names_are_skipped():
    incoming = {"tags": [{"id": 1, "name": "test"}, {"id": 2, "name": "  "}, {"id": 3, "name": None},
                         {"id": 4, "name": "Marine Welding"}]}
    tags = _run(_pair(_BASE, incoming)).details["by_concept_level"]["tags"]
    assert tags["relevant_concepts_reviewed"] == 1
    assert [item["incoming_id"] for item in tags["skipped_invalid"]] == [1, 2, 3]


def test_match_to_inactive_baseline_row_is_flagged():
    baseline = {**_BASE, "categories": [{"id": 1, "name": "Finance", "status": "0"}]}
    categories = _run(_pair(baseline, {"categories": [{"id": 9, "name": "finance"}]})).details[
        "by_concept_level"]["categories"]
    assert categories["matched_inactive"] == 1
    assert categories["exact_match_details"][0]["baseline_status"] == "0"


def test_curve_compares_each_source_against_everything_seen_before():
    new_source = {"tags": [{"id": 1, "name": "Marine Welding"}, {"id": 2, "name": "Budgeting"}]}
    datasets = _pair(_BASE, new_source)
    datasets["repeat"] = TaxonomyDataset(name="repeat", **new_source)
    result = SaturationMetric().run(datasets, {"baseline": "original", "incoming": ["skillsfuture", "repeat"]})

    assert result.status == "ok"
    curve = result.details["curve"]
    assert [point["source"] for point in curve] == ["skillsfuture", "repeat"]
    assert curve[0]["new_rate_max"] == 0.5
    assert curve[1]["new_rate_max"] == 0.0
    assert curve[1]["cumulative_known_concepts"] == curve[0]["cumulative_known_concepts"]
    exact = result.details["steps"][1]["by_concept_level"]["tags"]["exact_match_details"]
    assert {match["matched_source"] for match in exact} == {"original", "skillsfuture"}
    # Top-level keys describe the last step.
    assert result.details["by_concept_level"] == result.details["steps"][1]["by_concept_level"]
    assert result.details["incoming"] == ["skillsfuture", "repeat"]


def test_single_incoming_name_still_produces_one_step_curve():
    result = _run(_datasets())
    assert result.details["incoming"] == ["skillsfuture"]
    assert len(result.details["curve"]) == len(result.details["steps"]) == 1


@pytest.mark.parametrize("incoming", [[], ["skillsfuture", "skillsfuture"], ["original"], 5, [""]])
def test_invalid_incoming_lists_report_an_error(incoming):
    result = SaturationMetric().run(_datasets(), {"baseline": "original", "incoming": incoming})
    assert result.status == "error"


def test_other_level_close_spelling_confirmed_by_meaning(monkeypatch):
    _fake_matcher(monkeypatch, {"evaluation": ("valuation", 0.9)})
    tags = _run(_pair(_BASE, {"tags": [{"id": 1, "name": "Evaluation"}]}), semantic_model="m").details[
        "by_concept_level"]["tags"]
    match = tags["fuzzy_match_details"][0]
    assert (match["match_type"], match["baseline_level"], match["baseline_name"]) == (
        "close_spelling", "specialities", "Valuation")


def test_other_level_meaning_only_match(monkeypatch):
    _fake_matcher(monkeypatch, {"asset appraisal": ("valuation", 0.8)})
    tags = _run(_pair(_BASE, {"tags": [{"id": 1, "name": "Asset Appraisal"}]}), semantic_model="m").details[
        "by_concept_level"]["tags"]
    match = tags["semantic_match_details"][0]
    assert (match["baseline_level"], match["semantic_score"]) == ("specialities", 0.8)
    assert match["recommended_action"] == "SKIP_SEMANTIC_DUPLICATE"
    assert tags["new_rate_max"] == 0.0


def test_exact_other_level_match_beats_same_level_meaning_match(monkeypatch):
    _fake_matcher(monkeypatch, {"valuation": ("budgeting", 0.95)})
    tags = _run(_pair(_BASE, {"tags": [{"id": 1, "name": "Valuation"}]}), semantic_model="m").details[
        "by_concept_level"]["tags"]
    assert tags["semantic_candidate_matches"] == 0
    assert tags["exact_match_details"][0]["baseline_level"] == "specialities"


def test_same_level_wins_within_a_tier(monkeypatch):
    baseline = {**_BASE, "specialities": [{"id": 10, "name": "Budget Planning"}]}
    _fake_matcher(monkeypatch, {"cost control": [("budgeting", 0.8), ("budget planning", 0.9)]})
    tags = _run(_pair(baseline, {"tags": [{"id": 1, "name": "Cost Control"}]}), semantic_model="m").details[
        "by_concept_level"]["tags"]
    assert tags["semantic_match_details"][0]["baseline_name"] == "budgeting"
    assert tags["other_level_matches"] == 0


def test_add_candidate_requires_nothing_close_at_any_level():
    datasets = _pair(_BASE, {"tags": [{"id": 1, "name": "Valuation Review"}]})
    tags = _run(datasets).details["by_concept_level"]["tags"]
    concept = _ambiguous(tags, "Valuation Review")
    assert (concept["recommended_action"], concept["reason"]) == ("REVIEW_AMBIGUOUS", "partial_similarity")
    assert concept["closest_baseline_level"] == "specialities"

    strict = _run(datasets, cross_level=False).details["by_concept_level"]["tags"]
    assert _ambiguous(strict, "Valuation Review")["recommended_action"] == "ADD_CANDIDATE"


def test_cross_level_off_is_strict_level_by_level():
    result = _run(_pair(_BASE, {"tags": [{"id": 1, "name": "Valuation"}]}), cross_level=False)
    assert result.details["cross_level"] is False
    assert result.details["by_concept_level"]["tags"]["other_level_matches"] == 0


def test_non_bool_cross_level_reports_an_error():
    assert _run(_datasets(), cross_level="yes").status == "error"


_HEALTH_ML = {
    "categories": [{"id": 1, "name": "Healthcare"}, {"id": 2, "name": "Accountancy"}],
    "specialities": [
        {"id": 10, "name": "Machine Learning", "category_id": 1},
        {"id": 11, "name": "Head of Treasury", "category_id": 2},
    ],
    "tags": [{"id": 100, "name": "python"}],
}


def _sf_roles(*roles):
    """Incoming source with sectors and job roles given as (role, sector) pairs."""
    sectors = list(dict.fromkeys(sector for _, sector in roles))
    return {
        "categories": [{"id": i, "name": sector} for i, sector in enumerate(sectors, start=1)],
        "specialities": [
            {"id": 100 + i, "name": role, "category_id": sectors.index(sector) + 1}
            for i, (role, sector) in enumerate(roles)
        ],
    }


def test_known_concept_under_a_new_parent_is_a_new_placement():
    level = _run(_pair(_HEALTH_ML, _sf_roles(("Machine Learning", "Finance")))).details[
        "by_concept_level"]["specialities"]
    assert level["recommended_action_counts"]["REVIEW_NEW_PLACEMENT"] == 1
    placement = level["new_placement_details"][0]
    assert placement["unmatched_incoming_parents"] == [{"id": 1, "name": "Finance"}]
    assert placement["existing_parents"] == ["Healthcare"]
    assert placement["reason"] == "parent_differs"
    # A known concept is not a new item.
    assert (level["new_rate_max"], level["new_placement_rate"]) == (0.0, 1.0)


def test_known_concept_under_the_same_parent_is_a_duplicate():
    level = _run(_pair(_HEALTH_ML, _sf_roles(("machine learning", "Healthcare")))).details[
        "by_concept_level"]["specialities"]
    assert level["new_placements"] == 0
    assert level["exact_match_details"][0]["parent_check"] == "same_parent"


def test_parent_similar_in_meaning_counts_as_the_same_parent(monkeypatch):
    _fake_matcher(monkeypatch, {}, parent_similarity={("health services", "healthcare"): 0.8})
    level = _run(_pair(_HEALTH_ML, _sf_roles(("Machine Learning", "Health Services"))), semantic_model="m").details[
        "by_concept_level"]["specialities"]
    assert level["new_placements"] == 0


def test_every_incoming_parent_must_be_covered():
    incoming = _sf_roles(("Head of Treasury", "Accountancy"), ("Head of Treasury", "Wholesale Trade"))
    level = _run(_pair(_HEALTH_ML, incoming)).details["by_concept_level"]["specialities"]
    placement = level["new_placement_details"][0]
    assert [parent["name"] for parent in placement["unmatched_incoming_parents"]] == ["Wholesale Trade"]
    assert placement["incoming_ids"] == [100, 101]


def test_known_concept_without_parents_is_flagged():
    baseline = {**_HEALTH_ML, "specialities": [{"id": 10, "name": "Machine Learning", "category_id": 99}]}
    level = _run(_pair(baseline, _sf_roles(("Machine Learning", "Finance")))).details[
        "by_concept_level"]["specialities"]
    assert level["new_placement_details"][0]["reason"] == "existing_has_no_parent"


def test_incoming_without_parent_skips_on_concept_match():
    categories = _run(_pair(_HEALTH_ML, {"categories": [{"id": 1, "name": "Healthcare"}]})).details[
        "by_concept_level"]["categories"]
    assert categories["exact_match_details"][0]["parent_check"] == "no_incoming_parent"
    assert categories["placements_checked"] == 0


def test_tags_skip_the_parent_check():
    incoming = {
        **_sf_roles(("Data Scientist", "Finance")),
        "tags": [{"id": 1, "name": "Python"}],
        "speciality_tags": [{"id": 1, "speciality_id": 100, "tag_id": 1}],
    }
    tags = _run(_pair(_HEALTH_ML, incoming)).details["by_concept_level"]["tags"]
    assert tags["exact_match_details"][0]["parent_check"] == "not_checked_for_tags"
    assert tags["new_placements"] == 0


def test_placements_from_earlier_sources_are_not_flagged_again():
    datasets = _pair(_HEALTH_ML, _sf_roles(("Machine Learning", "Finance")))
    datasets["repeat"] = TaxonomyDataset(name="repeat", **_sf_roles(("Machine Learning", "Finance")))
    curve = SaturationMetric().run(
        datasets, {"baseline": "original", "incoming": ["skillsfuture", "repeat"]}
    ).details["curve"]
    assert [point["new_placements"] for point in curve] == [1, 0]


def test_name_repeated_at_two_levels_of_one_source_counts_once():
    incoming = {"categories": [{"id": 1, "name": "Workplace Safety and Health"}],
                "tags": [{"id": 9, "name": "Workplace Safety & Health"}]}
    result = _run(_pair(_BASE, incoming))
    tags = result.details["by_concept_level"]["tags"]
    assert tags["relevant_concepts_reviewed"] == 0
    assert tags["repeated_in_source_other_level"] == [
        {"incoming_ids": [9], "incoming_name": "Workplace Safety & Health", "first_counted_level": "categories"}
    ]
    assert result.details["overall"]["relevant_concepts_reviewed"] == 1


def test_stricter_meaning_threshold_for_one_level(monkeypatch):
    baseline = {**_BASE, "specialities": [{"id": 10, "name": "Risk Management", "category_id": 1}]}
    incoming = _sf_roles(("Risk Manager", "Finance"))
    _fake_matcher(monkeypatch, {"risk manager": ("risk management", 0.75)})

    loose = _run(_pair(baseline, incoming), semantic_model="m").details["by_concept_level"]["specialities"]
    assert loose["semantic_candidate_matches"] == 1

    strict = _run(
        _pair(baseline, incoming), semantic_model="m", semantic_threshold_by_level={"specialities": 0.8}
    ).details
    level = strict["by_concept_level"]["specialities"]
    assert level["semantic_candidate_matches"] == 0
    assert _ambiguous(level, "Risk Manager")["recommended_action"] == "REVIEW_AMBIGUOUS"
    assert strict["semantic_threshold_by_level"] == {"specialities": 0.8}


@pytest.mark.parametrize("value", [{"roles": 0.8}, {"specialities": 1.5}, [0.8], {"tags": "high"}])
def test_invalid_semantic_threshold_by_level_reports_an_error(value):
    assert _run(_datasets(), semantic_threshold_by_level=value).status == "error"


def test_duplicates_also_carry_their_incoming_parents():
    incoming = {
        **_sf_roles(("Machine Learning", "Healthcare"), ("Data Scientist", "Finance")),
        "tags": [{"id": 1, "name": "Python"}],
        "speciality_tags": [{"id": 1, "speciality_id": 100, "tag_id": 1}, {"id": 2, "speciality_id": 101, "tag_id": 1}],
    }
    levels = _run(_pair(_HEALTH_ML, incoming)).details["by_concept_level"]
    role = levels["specialities"]["exact_match_details"][0]
    assert (role["parent_status"], role["parents"]) == ("HAS_PARENT", [{"id": 1, "name": "Healthcare"}])
    tag = levels["tags"]["exact_match_details"][0]
    assert tag["parent_level"] == "specialities"
    assert [parent["name"] for parent in tag["parents"]] == ["Machine Learning", "Data Scientist"]
    # Every reviewed tag counts, not only flagged ones.
    assert levels["tags"]["parent_status_counts"] == {"HAS_PARENT": 1, "NO_PARENT": 0}
    assert levels["tags"]["parent_status_counts_flagged"] == {"HAS_PARENT": 0, "NO_PARENT": 0}


def test_cross_level_match_with_incomparable_parents_is_review_other_level():
    # An SF job role (parent: sector) matching only an original tag (parents:
    # specialities) - the parents are different kinds and can't be compared.
    baseline = {**_HEALTH_ML, "speciality_tags": [{"id": 1, "speciality_id": 10, "tag_id": 100}]}
    level = _run(_pair(baseline, _sf_roles(("Python", "Finance")))).details["by_concept_level"]["specialities"]
    match = level["other_level_match_details"][0]
    assert (match["recommended_action"], match["reason"], match["baseline_level"]) == (
        "REVIEW_OTHER_LEVEL", "parent_not_comparable", "tags")
    assert match["parents"] == [{"id": 1, "name": "Finance"}]
    assert (level["new_placements"], level["placements_checked"], level["new_rate_max"]) == (0, 0, 0.0)
    assert level["parent_status_counts_flagged"] == {"HAS_PARENT": 1, "NO_PARENT": 0}


def test_incoming_category_matching_a_known_speciality_is_not_a_new_placement():
    # The reviewer's case: a category with its own service parent exact-matches
    # a known speciality (whose parent is a category).
    incoming = {"services": [{"id": 1, "name": "Industry"}],
                "categories": [{"id": 5, "name": "Machine Learning", "service_id": 1}]}
    result = _run(_pair(_HEALTH_ML, incoming))
    categories = result.details["by_concept_level"]["categories"]
    assert categories["new_placements"] == 0
    assert categories["other_level_match_details"][0]["match_type"] == "exact"
    assert result.details["curve"][0]["review_other_level"] == 1


def test_placeholder_parents_are_ignored_on_both_sides():
    baseline = {
        "categories": [{"id": 1, "name": "test"}],
        "specialities": [{"id": 10, "name": "Machine Learning", "category_id": 1}],
        "tags": [{"id": 100, "name": "python"}],
    }
    level = _run(_pair(baseline, _sf_roles(("Machine Learning", "test")))).details["by_concept_level"]["specialities"]
    assert level["new_placements"] == 0
    match = level["exact_match_details"][0]
    assert (match["parent_check"], match["parent_status"]) == ("no_incoming_parent", "NO_PARENT")


def test_best_matches_reuses_cached_embeddings():
    class CountingModel:
        def __init__(self):
            self.encoded: list[str] = []

        def encode(self, names, normalize_embeddings):
            self.encoded.extend(names)
            return [np.array([1.0, float(len(name))]) / np.linalg.norm([1.0, float(len(name))]) for name in names]

    matcher = object.__new__(saturation.SemanticMatcher)
    matcher.model, matcher._cache = CountingModel(), {}
    first = matcher.best_matches(["ab", "abcd"], ["abc", "abcd"])
    assert first[1][0] == "abcd"
    matcher.best_matches(["abcd"], ["abc", "abcd"])
    matcher.similarity(["ab"], ["abc"])
    # Each distinct name is encoded exactly once across all calls.
    assert sorted(matcher.model.encoded) == ["ab", "abc", "abcd"]
    assert matcher.best_matches([], ["abc"]) == []
    assert matcher.best_matches(["ab"], []) == [(None, None)]


def test_top_matches_returns_the_k_closest_choices_in_order():
    class UnitModel:
        def encode(self, names, normalize_embeddings):
            vectors = {"a": [1.0, 0.0], "b": [0.8, 0.6], "c": [0.0, 1.0], "q": [1.0, 0.0]}
            return [np.array(vectors[name]) for name in names]

    matcher = object.__new__(saturation.SemanticMatcher)
    matcher.model, matcher._cache = UnitModel(), {}
    assert matcher.top_matches(["q"], ["c", "b", "a"], 2) == [[("a", 1.0), ("b", 0.8)]]
    assert matcher.top_matches([], ["a"], 2) == []
    assert matcher.top_matches(["q"], [], 2) == [[]]


# --- Optional Decisions API judge -------------------------------------------------

_JUDGE_BASE = {
    "categories": [{"id": 1, "name": "Finance"}],
    "specialities": [
        {"id": 10, "name": "Corporate Finance", "category_id": 1},
        {"id": 11, "name": "Trade Marketing", "category_id": 1},
    ],
    "tags": [{"id": 100, "name": "prompt"}, {"id": 101, "name": "budgeting"}],
    "speciality_tags": [{"speciality_id": 10, "tag_id": 101}],
}
_JUDGE_INCOMING = {
    "specialities": [{"id": 50, "name": "Trade Finance Manager"}],
    "tags": [
        {"id": 1, "name": "Prompt Engineering"},
        {"id": 2, "name": "Render Management"},
        {"id": 3, "name": "Trade Finance Management"},
        {"id": 4, "name": "Carbon Ledger Audit"},
        {"id": 5, "name": "Zymurgy"},
    ],
    "speciality_tags": [{"speciality_id": 50, "tag_id": 3}],
}
# Meaning scores in the ambiguous band (0.5-0.7) for every tag but Zymurgy, which nothing is close to.
_JUDGE_MEANING = {
    "prompt engineering": [("prompt", 0.68), ("trade marketing", 0.2)],
    "render management": [("trade marketing", 0.55), ("corporate finance", 0.3)],
    "trade finance management": [("corporate finance", 0.67), ("trade marketing", 0.6)],
    "carbon ledger audit": [("budgeting", 0.52)],
    "zymurgy": [("budgeting", 0.1)],
    "trade finance manager": [("corporate finance", 0.6)],
}


class FakeJudge:
    """Answers by concept name; records the questions it was asked."""

    def __init__(self, answers):
        self.model = "fake-judge"
        self.answers = answers
        self.questions = []

    def judge(self, questions):
        from taxonomy_pipeline.matching.judge import JudgeVerdict

        self.questions.extend(questions)
        verdicts = []
        for question in questions:
            answer = self.answers.get(question.name)
            if isinstance(answer, Exception):
                raise answer
            choice, probability, error = answer or (None, None, "no answer")
            verdicts.append(JudgeVerdict(choice=choice, probability=probability, error=error))
        return verdicts


def _judge_matcher(monkeypatch):
    class FakeSemanticMatcher:
        def __init__(self, model_name, revision=None):
            pass

        def _scores(self, query):
            return _JUDGE_MEANING.get(query, [])

        def best_matches(self, queries, choices):
            return [next(((n, s) for n, s in self._scores(q) if n in choices), (None, None)) for q in queries]

        def top_matches(self, queries, choices, k):
            return [[(n, s) for n, s in self._scores(q) if n in choices][:k] for q in queries]

        def similarity(self, queries, choices):
            return np.zeros((len(queries), len(choices)))

    monkeypatch.setattr(saturation, "SemanticMatcher", FakeSemanticMatcher)


def _with_judge(monkeypatch, answers, **judge):
    _judge_matcher(monkeypatch)
    fake = FakeJudge(answers)
    built = {}

    def factory(model, *, workers, cache_path):
        built.update(model=model, workers=workers, cache_path=cache_path)
        return fake

    monkeypatch.setattr(saturation, "DecisionsJudge", factory)
    result = _run(_pair(_JUDGE_BASE, _JUDGE_INCOMING), semantic_model="m", judge=judge)
    return result, fake, built


_ANSWERS = {
    "Prompt Engineering": ("1", 0.93, None),          # the same as tag "prompt"
    "Render Management": ("none", 0.95, None),       # only sounds like Trade Marketing
    "Trade Finance Management": ("1", 0.55, None),   # leaning, but not confident enough
    "Carbon Ledger Audit": (None, None, "API error 500: boom"),
}


def test_judge_settles_ambiguous_tags_and_reports_both_counts(monkeypatch):
    result, _, _ = _with_judge(monkeypatch, _ANSWERS)
    assert result.status == "ok"
    tags = result.details["by_concept_level"]["tags"]

    assert tags["review_ambiguous_before_judge"] == 4
    assert tags["judge_counts"] == {"asked": 4, "judged_duplicate": 1, "judged_new": 1, "unsure": 1, "failed": 1}
    assert tags["recommended_action_counts"]["REVIEW_AMBIGUOUS"] == 2
    assert tags["recommended_action_counts"]["ADD_CANDIDATE"] == 2
    assert tags["recommended_action_counts"]["SKIP_SEMANTIC_DUPLICATE"] == 1

    duplicate = tags["judged_match_details"][0]
    assert (duplicate["incoming_name"], duplicate["baseline_name"]) == ("Prompt Engineering", "prompt")
    assert duplicate["match_type"] == "judged" and duplicate["recommended_action"] == "SKIP_SEMANTIC_DUPLICATE"
    assert duplicate["judge_probability"] == 0.93 and duplicate["semantic_score"] == 0.68
    assert duplicate["judge"] == {"model": "fake-judge", "choice": "1", "probability": 0.93,
                                  "candidates": ["prompt", "Trade Marketing"]}
    assert "Prompt Engineering" not in {entry["incoming_name"] for entry in tags["ambiguous_concepts"]}

    new = _ambiguous(tags, "Render Management")
    assert (new["recommended_action"], new["reason"]) == ("ADD_CANDIDATE", "judged_new")
    assert new["judge"] == {"model": "fake-judge", "choice": "none", "probability": 0.95,
                            "candidates": ["Trade Marketing", "Corporate Finance"]}
    assert _ambiguous(tags, "Trade Finance Management")["reason"] == "judge_unsure"
    failed = _ambiguous(tags, "Carbon Ledger Audit")
    assert (failed["recommended_action"], failed["reason"]) == ("REVIEW_AMBIGUOUS", "judge_failed")
    assert failed["judge"]["error"] == "API error 500: boom"

    point = result.details["curve"][-1]
    assert point["review_ambiguous_before_judge"] == 5  # 4 tags + 1 speciality the judge didn't look at
    assert point["review_ambiguous"] == 3
    # Out of the 4 tags it was asked about, not the 5 ambiguous concepts across every level.
    assert "Judge (gpt-6-luna) settled 2 of 4 ambiguous concepts it was asked about (tags)" in result.summary
    assert result.details["judge"]["model"] == "gpt-6-luna"


def test_judge_sees_only_review_ambiguous_concepts_with_their_context(monkeypatch):
    _, fake, _ = _with_judge(monkeypatch, _ANSWERS)
    asked = {question.name: question for question in fake.questions}
    assert set(asked) == {"Prompt Engineering", "Render Management", "Trade Finance Management", "Carbon Ledger Audit"}
    question = asked["Trade Finance Management"]
    assert question.level == "tags" and question.parents == ("Trade Finance Manager",)
    assert [(c.name, c.level, c.parents) for c in question.candidates] == [
        ("Corporate Finance", "specialities", ("Finance",)), ("Trade Marketing", "specialities", ("Finance",)),
    ]
    assert [c.parents for c in asked["Carbon Ledger Audit"].candidates] == [("Corporate Finance",)]


def test_judge_only_runs_on_the_configured_levels(monkeypatch):
    result, fake, _ = _with_judge(monkeypatch, {"Trade Finance Manager": ("none", 0.9, None)}, levels=["specialities"])
    levels = result.details["by_concept_level"]
    assert [question.name for question in fake.questions] == ["Trade Finance Manager"]
    assert _ambiguous(levels["specialities"], "Trade Finance Manager")["reason"] == "judged_new"
    assert "judge_counts" not in levels["tags"]


def test_judge_thresholds_and_settings_are_passed_through(monkeypatch):
    result, fake, built = _with_judge(
        monkeypatch, _ANSWERS, same_at_or_above=0.5, candidates=1, cache="judge_cache.json", workers=2,
    )
    tags = result.details["by_concept_level"]["tags"]
    assert tags["judge_counts"]["judged_duplicate"] == 2  # Trade Finance Management's 0.55 now counts
    assert all(len(question.candidates) == 1 for question in fake.questions)
    assert built == {"model": "gpt-6-luna", "workers": 2, "cache_path": "judge_cache.json"}


def test_without_a_judge_the_report_has_no_judge_fields(monkeypatch):
    _judge_matcher(monkeypatch)
    result = _run(_pair(_JUDGE_BASE, _JUDGE_INCOMING), semantic_model="m")
    tags = result.details["by_concept_level"]["tags"]
    assert tags["recommended_action_counts"]["REVIEW_AMBIGUOUS"] == 4
    assert not {"judge_counts", "review_ambiguous_before_judge", "judged_match_details"} & set(tags)
    assert "judge" not in result.details and "review_ambiguous_before_judge" not in result.details["curve"][-1]
    assert all("judge" not in entry for entry in tags["ambiguous_concepts"])


def test_judge_needs_the_meaning_model():
    result = _run(_pair(_JUDGE_BASE, _JUDGE_INCOMING), judge={})
    assert result.status == "error" and "semantic_model" in result.summary


@pytest.mark.parametrize("judge", [
    "yes", {"modle": "x"}, {"model": ""}, {"candidates": 0}, {"workers": True},
    {"same_at_or_above": 1.5}, {"levels": []}, {"levels": ["services"]}, {"cache": 3},
])
def test_invalid_judge_settings_report_an_error(monkeypatch, judge):
    _judge_matcher(monkeypatch)
    result = _run(_pair(_JUDGE_BASE, _JUDGE_INCOMING), semantic_model="m", judge=judge)
    assert result.status == "error" and "judge" in result.summary


def test_a_judge_that_cannot_run_reports_an_error(monkeypatch):
    from taxonomy_pipeline.matching.judge import JudgeError

    result, _, _ = _with_judge(monkeypatch, {"Prompt Engineering": JudgeError("rejected (401): bad key")})
    assert result.status == "error" and result.summary == "judge: rejected (401): bad key"

    _judge_matcher(monkeypatch)

    def no_key(model, *, workers, cache_path):
        raise JudgeError("needs a real OPENAI_API_KEY")

    monkeypatch.setattr(saturation, "DecisionsJudge", no_key)
    result = _run(_pair(_JUDGE_BASE, _JUDGE_INCOMING), semantic_model="m", judge={})
    assert result.status == "error" and "OPENAI_API_KEY" in result.summary
