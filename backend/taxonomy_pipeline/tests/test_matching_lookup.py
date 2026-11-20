"""Ranking one term against concept names: which matches count as exact, near, or nothing."""
from __future__ import annotations

import numpy as np

from taxonomy_pipeline.matching.lookup import NameIndex


class FakeMatcher:
    """Scores given by name, so a test needs no sentence model."""

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray:
        return np.array([[self.scores.get(choice, 0.0) for choice in choices] for _ in queries])


def _types(matches: list, names: list[str]) -> list[tuple[str, str]]:
    return [(names[match.index], match.match_type) for match in matches]


def test_a_name_differing_only_in_case_spacing_or_punctuation_is_exact() -> None:
    names = ["Tax  Advisory."]

    assert _types(NameIndex(names).match("tax advisory"), names) == [("Tax  Advisory.", "exact")]


def test_ampersand_spelling_and_plural_differences_are_variants() -> None:
    names = ["Data & Analytics", "Business Optimisation", "Audit Reports"]
    index = NameIndex(names)

    assert _types(index.match("data and analytics"), names) == [("Data & Analytics", "variant")]
    assert _types(index.match("business optimization"), names) == [("Business Optimisation", "variant")]
    assert _types(index.match("audit report"), names) == [("Audit Reports", "variant")]


def test_a_close_misspelling_is_a_near_match_with_its_score() -> None:
    [match] = NameIndex(["Accounting"]).match("Acounting")

    assert match.match_type == "lexical"
    assert not match.exact
    assert 85 <= match.lexical_score < 100
    assert match.semantic_score is None


def test_a_name_below_the_score_threshold_is_not_returned() -> None:
    index = NameIndex(["Accounting"])

    assert index.match("Acounting", min_score=99) == []
    assert index.match("Marketing") == []


def test_exact_matches_rank_before_variants_and_near_matches_closest_first() -> None:
    names = ["Marketing Plan", "Marketing Plans", "marketing plan", "Marketing Plam"]

    assert _types(NameIndex(names).match("Marketing Plan"), names) == [
        ("Marketing Plan", "exact"), ("marketing plan", "exact"),
        ("Marketing Plans", "variant"), ("Marketing Plam", "lexical"),
    ]


def test_every_duplicate_row_with_the_same_name_is_returned() -> None:
    names = ["Tax", "Tax"]

    assert [match.index for match in NameIndex(names).match("tax")] == [0, 1]


def test_a_matcher_adds_names_that_mean_the_same_above_its_threshold() -> None:
    names = ["Machine Learning", "Cooking"]
    matcher = FakeMatcher({"Machine Learning": 0.82, "Cooking": 0.2})

    [match] = NameIndex(names).match("ML", matcher=matcher)

    assert (names[match.index], match.match_type, match.semantic_score) == ("Machine Learning", "semantic", 0.82)


def test_a_name_alike_in_spelling_and_meaning_is_lexical_with_both_scores() -> None:
    [match] = NameIndex(["Accounting"]).match("Acounting", matcher=FakeMatcher({"Accounting": 0.9}))

    assert match.match_type == "lexical"
    assert match.semantic_score == 0.9


def test_an_empty_index_matches_nothing() -> None:
    assert NameIndex([]).match("tax") == []
