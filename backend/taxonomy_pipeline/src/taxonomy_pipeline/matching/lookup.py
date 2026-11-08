"""Rank one proposed term against a set of concept names, for the tag lookup.

Screening and saturation compare whole taxonomies in batches; a lookup compares a
single term on every request, so `NameIndex` folds the names once and is kept
between requests. It uses the same folds as the other stages (see `names.py` for
what each one answers), so "already exists" here agrees with `merge-preview`.

A match is exact when `normalize` agrees, a variant when only `match_key` does, and
otherwise near. Scores and thresholds are review evidence, not probabilities: they
are uncalibrated (#47), so a caller should treat a near match as "look at this".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .names import lexical_scores, match_key, normalize
from .semantic import SimilarityMatcher

MatchType = Literal["exact", "variant", "lexical", "semantic"]
# Integrity's fuzzy threshold: the spelling closeness it already reports as a likely duplicate.
DEFAULT_MIN_SCORE = 85.0
# Hierarchy screening's default, for the same question: could this name mean an existing concept?
SEMANTIC_THRESHOLD = 0.7


@dataclass(frozen=True)
class NameMatch:
    """One name a term matched, by its position in the index's names."""

    index: int
    match_type: MatchType
    lexical_score: float  # RapidFuzz ratio on `match_key`, 0-100
    semantic_score: float | None = None  # cosine, -1 to 1; only when a matcher was given

    @property
    def exact(self) -> bool:
        return self.match_type in ("exact", "variant")


class NameIndex:
    """Concept names with their folds computed once."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        self._normalized = [normalize(name) for name in names]
        self._keys = [match_key(name) for name in names]

    def match(
        self, term: str, *, min_score: float = DEFAULT_MIN_SCORE, matcher: SimilarityMatcher | None = None,
        semantic_threshold: float = SEMANTIC_THRESHOLD,
    ) -> list[NameMatch]:
        """Every name matching `term`: exact, then variants, then near matches, closest first.

        A name both spelled and meaning alike counts as `lexical`, carrying both scores. Equal
        ranks keep the index's order, so results are stable between requests.
        """
        if not self.names:
            return []
        normalized, key = normalize(term), match_key(term)
        lexical = lexical_scores([key], self._keys)[0]
        semantic = matcher.similarity([term], self.names)[0] if matcher else None

        matches = []
        for index in range(len(self.names)):
            meaning = None if semantic is None else round(float(semantic[index]), 4)
            match_type = self._classify(
                index, normalized, key, float(lexical[index]), meaning, min_score, semantic_threshold,
            )
            if match_type:
                matches.append(NameMatch(index, match_type, round(float(lexical[index]), 1), meaning))
        return sorted(matches, key=_rank)

    def _classify(
        self, index: int, normalized: str, key: str, lexical: float, meaning: float | None,
        min_score: float, semantic_threshold: float,
    ) -> MatchType | None:
        if self._normalized[index] == normalized:
            return "exact"
        if self._keys[index] == key:
            return "variant"
        if lexical >= min_score:
            return "lexical"
        if meaning is not None and meaning >= semantic_threshold:
            return "semantic"
        return None


def _rank(match: NameMatch) -> tuple[int, float]:
    order = {"exact": 0, "variant": 1}.get(match.match_type, 2)
    return order, -max(match.lexical_score / 100, match.semantic_score or 0.0)
