import numpy as np
import pytest

from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.metrics import saturation
from taxonomy_pipeline.metrics.saturation import SaturationMetric


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
        def __init__(self, model_name):
            assert model_name == "test-model"

        def best_matches(self, queries, choices):
            assert choices
            results = {
                "marine services": ("financial services", 0.20),
                "payroll process": ("payroll processing", 0.91),
                "carbon accounting": ("payroll processing", 0.50),
                "carbon reporting": ("accounting", 0.81),
            }
            return [results[query] for query in queries]

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
        def __init__(self, model_name):
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
