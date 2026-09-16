"""Source-by-source taxonomy saturation through concept novelty.

For every named category, speciality, and tag in an incoming taxonomy, find
the closest concept at the same level in a baseline taxonomy. Exact normalized
names are treated as represented; otherwise RapidFuzz supplies a lexical
candidate. Optionally, a sentence-embedding model supplies a semantic
candidate for concepts that remain unmatched. Concepts below both thresholds
are *ambiguous*, not automatically confirmed additions: a reviewer decides
whether they are truly new, out of scope, or semantic equivalents with
different names.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import numpy as np
from rapidfuzz import fuzz, process

from ..datasets.base import LoadedDataset, TaxonomyDataset
from ..models import MetricResult
from .base import Metric
from .integrity import normalize

CONCEPT_LEVELS = ("categories", "specialities", "tags")
SKIP_DUPLICATE = "SKIP_DUPLICATE"
SKIP_SEMANTIC_DUPLICATE = "SKIP_SEMANTIC_DUPLICATE"
ADD_CANDIDATE = "ADD_CANDIDATE"
REVIEW_AMBIGUOUS = "REVIEW_AMBIGUOUS"


class SemanticMatcher:
    """Lazy sentence-embedding matcher, kept optional for lightweight runs."""

    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ValueError(
                "semantic matching requires the optional dependency; install with "
                '`pip install -e ".[dev,semantic]"`'
            ) from exc
        self.model = SentenceTransformer(model_name)

    def best_matches(self, queries: list[str], choices: list[str]) -> list[tuple[str | None, float | None]]:
        if not queries or not choices:
            return [(None, None) for _ in queries]
        embeddings = self.model.encode(queries + choices, normalize_embeddings=True)
        query_vectors = np.asarray(embeddings[: len(queries)])
        choice_vectors = np.asarray(embeddings[len(queries) :])
        scores = query_vectors @ choice_vectors.T
        best_indexes = np.argmax(scores, axis=1)
        return [(choices[index], round(float(scores[row, index]), 4)) for row, index in enumerate(best_indexes)]


def _unique_named_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one incoming row per normalized name so duplicate source rows do
    not artificially lower the new-concept rate.
    """
    seen: set[str] = set()
    unique = []
    for row in rows:
        name = row.get("name")
        key = normalize(name)
        if key and key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _compare_level(
    baseline_rows: list[dict[str, Any]],
    incoming_rows: list[dict[str, Any]],
    threshold: float,
    semantic_matcher: SemanticMatcher | None,
    semantic_threshold: float,
    add_candidate_lexical_threshold: float,
    add_candidate_semantic_threshold: float,
) -> dict[str, Any]:
    baseline = _unique_named_rows(baseline_rows)
    incoming = _unique_named_rows(incoming_rows)
    baseline_by_name = {normalize(row["name"]): row for row in baseline}
    baseline_names = list(baseline_by_name)

    exact_matches: list[dict[str, Any]] = []
    fuzzy_matches: list[dict[str, Any]] = []
    semantic_matches: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    lexical_unmatched: list[tuple[dict[str, Any], str]] = []

    for row in incoming:
        original_name = str(row["name"])
        normalized_name = normalize(original_name)
        exact = baseline_by_name.get(normalized_name)
        if exact:
            exact_matches.append(
                {
                    "incoming_id": row.get("id"),
                    "incoming_name": original_name,
                    "baseline_id": exact.get("id"),
                    "baseline_name": exact.get("name"),
                    "score": 100.0,
                    "recommended_action": SKIP_DUPLICATE,
                }
            )
            continue

        lexical_unmatched.append((row, normalized_name))

    lexical_matches: list[tuple[str | None, float | None]]
    if lexical_unmatched:
        scores = process.cdist(
            [normalized_name for _, normalized_name in lexical_unmatched],
            baseline_names,
            scorer=fuzz.ratio,
        )
        best_indexes = np.argmax(scores, axis=1)
        lexical_matches = [
            (baseline_names[index], float(scores[row_index, index]))
            for row_index, index in enumerate(best_indexes)
        ]
    else:
        lexical_matches = []

    semantic_unmatched: list[tuple[dict[str, Any], tuple[str | None, float | None]]] = []
    for (row, _), (lexical_name, lexical_score) in zip(lexical_unmatched, lexical_matches, strict=True):
        original_name = str(row["name"])
        if lexical_name is not None and lexical_score is not None and lexical_score >= threshold:
            matched = baseline_by_name[lexical_name]
            fuzzy_matches.append(
                {
                    "incoming_id": row.get("id"),
                    "incoming_name": original_name,
                    "baseline_id": matched.get("id"),
                    "baseline_name": matched.get("name"),
                    "score": round(lexical_score, 1),
                    "recommended_action": SKIP_DUPLICATE,
                }
            )
        else:
            semantic_unmatched.append((row, (lexical_name, lexical_score)))

    semantic_results = (
        semantic_matcher.best_matches(
            [normalize(str(row["name"])) for row, _ in semantic_unmatched], baseline_names
        )
        if semantic_matcher
        else [(None, None)] * len(semantic_unmatched)
    )
    for (row, lexical_best), (semantic_name, semantic_score) in zip(
        semantic_unmatched, semantic_results, strict=True
    ):
        original_name = str(row["name"])
        if semantic_name is not None and semantic_score is not None and semantic_score >= semantic_threshold:
            matched = baseline_by_name[semantic_name]
            semantic_matches.append(
                {
                    "incoming_id": row.get("id"),
                    "incoming_name": original_name,
                    "baseline_id": matched.get("id"),
                    "baseline_name": matched.get("name"),
                    "semantic_score": semantic_score,
                    "closest_lexical_baseline_name": lexical_best[0] if lexical_best else None,
                    "closest_lexical_score": round(float(lexical_best[1]), 1) if lexical_best else None,
                    "recommended_action": SKIP_SEMANTIC_DUPLICATE,
                }
            )
        else:
            low_lexical = (
                lexical_best[1] is not None and lexical_best[1] < add_candidate_lexical_threshold
            )
            low_semantic = (
                semantic_score is not None and semantic_score < add_candidate_semantic_threshold
            )
            ambiguous.append(
                {
                    "incoming_id": row.get("id"),
                    "incoming_name": original_name,
                    "closest_baseline_name": lexical_best[0] if lexical_best else None,
                    "closest_baseline_score": round(float(lexical_best[1]), 1) if lexical_best else None,
                    "closest_semantic_baseline_name": semantic_name,
                    "closest_semantic_score": semantic_score,
                    "recommended_action": ADD_CANDIDATE if low_lexical and low_semantic else REVIEW_AMBIGUOUS,
                }
            )

    reviewed = len(incoming)
    ambiguous_count = len(ambiguous)
    action_counts = {
        SKIP_DUPLICATE: len(exact_matches) + len(fuzzy_matches),
        SKIP_SEMANTIC_DUPLICATE: len(semantic_matches),
        ADD_CANDIDATE: sum(item["recommended_action"] == ADD_CANDIDATE for item in ambiguous),
        REVIEW_AMBIGUOUS: sum(item["recommended_action"] == REVIEW_AMBIGUOUS for item in ambiguous),
    }
    return {
        "relevant_concepts_reviewed": reviewed,
        "exact_matches": len(exact_matches),
        "fuzzy_candidate_matches": len(fuzzy_matches),
        "semantic_candidate_matches": len(semantic_matches),
        "ambiguous": ambiguous_count,
        "ambiguous_rate": round(ambiguous_count / reviewed, 3) if reviewed else None,
        "recommended_action_counts": action_counts,
        "exact_match_details": exact_matches,
        "fuzzy_match_details": fuzzy_matches,
        "semantic_match_details": semantic_matches,
        "ambiguous_concepts": ambiguous,
    }


class SaturationMetric(Metric):
    id = "saturation"
    description = (
        "Incoming-source novelty against a baseline taxonomy. Uses normalized exact matches and "
        "RapidFuzz lexical matches, plus optional sentence-embedding semantic candidates; unmatched "
        "concepts with no high-confidence match are ambiguous and require review."
    )
    default_params = {
        "baseline": None,
        "incoming": None,
        "threshold": 90.0,
        "semantic_model": None,
        "semantic_threshold": 0.7,
        "add_candidate_lexical_threshold": 60.0,
        "add_candidate_semantic_threshold": 0.5,
    }

    def run(self, datasets: dict[str, LoadedDataset], params: dict[str, Any]) -> MetricResult:
        p = self.resolve_params(params)
        resolved = self.require_datasets(
            datasets,
            {"baseline": p["baseline"], "incoming": p["incoming"]},
            {"baseline": TaxonomyDataset, "incoming": TaxonomyDataset},
        )
        if isinstance(resolved, MetricResult):
            return resolved

        try:
            threshold = float(p["threshold"])
            if not math.isfinite(threshold) or not 0 <= threshold <= 100:
                raise ValueError("threshold must be finite and between 0 and 100")
            semantic_threshold = float(p["semantic_threshold"])
            if not math.isfinite(semantic_threshold) or not -1 <= semantic_threshold <= 1:
                raise ValueError("semantic_threshold must be finite and between -1 and 1")
            add_candidate_lexical_threshold = float(p["add_candidate_lexical_threshold"])
            if not math.isfinite(add_candidate_lexical_threshold) or not 0 <= add_candidate_lexical_threshold <= 100:
                raise ValueError("add_candidate_lexical_threshold must be finite and between 0 and 100")
            add_candidate_semantic_threshold = float(p["add_candidate_semantic_threshold"])
            if not math.isfinite(add_candidate_semantic_threshold) or not -1 <= add_candidate_semantic_threshold <= 1:
                raise ValueError("add_candidate_semantic_threshold must be finite and between -1 and 1")
        except (TypeError, ValueError) as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))

        baseline, incoming = resolved["baseline"], resolved["incoming"]
        empty_baseline_levels = [
            level
            for level in CONCEPT_LEVELS
            if _unique_named_rows(getattr(incoming, level)) and not _unique_named_rows(getattr(baseline, level))
        ]
        if empty_baseline_levels:
            return MetricResult(
                metric_id=self.id,
                status="error",
                summary=(
                    f"{baseline.name} has no named records for {', '.join(empty_baseline_levels)} while "
                    f"{incoming.name} does; check the dataset source or table_map."
                ),
            )
        try:
            semantic_matcher = SemanticMatcher(str(p["semantic_model"])) if p["semantic_model"] else None
        except ValueError as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        by_level = {
            level: _compare_level(
                getattr(baseline, level),
                getattr(incoming, level),
                threshold,
                semantic_matcher,
                semantic_threshold,
                add_candidate_lexical_threshold,
                add_candidate_semantic_threshold,
            )
            for level in CONCEPT_LEVELS
        }
        reviewed = sum(result["relevant_concepts_reviewed"] for result in by_level.values())
        if reviewed == 0:
            return MetricResult(
                metric_id=self.id,
                status="error",
                summary=(
                    f"{incoming.name} has no named categories, specialities, or tags to compare; "
                    "check the dataset source or table_map."
                ),
                details={
                    "baseline": baseline.name,
                    "incoming": incoming.name,
                    "by_concept_level": by_level,
                },
            )
        ambiguous = sum(result["ambiguous"] for result in by_level.values())
        rate = round(ambiguous / reviewed, 3)
        summary = (
            f"{incoming.name} against {baseline.name}: {ambiguous}/{reviewed} "
            f"({rate:.1%}) concepts have no high-confidence match and require review."
        )
        return MetricResult(
            metric_id=self.id,
            summary=summary,
            details={
                "baseline": baseline.name,
                "incoming": incoming.name,
                "threshold": threshold,
                    "semantic_model": p["semantic_model"],
                    "semantic_threshold": semantic_threshold if semantic_matcher else None,
                    "add_candidate_lexical_threshold": add_candidate_lexical_threshold,
                    "add_candidate_semantic_threshold": (
                        add_candidate_semantic_threshold if semantic_matcher else None
                    ),
                "overall": {
                    "relevant_concepts_reviewed": reviewed,
                    "ambiguous": ambiguous,
                    "ambiguous_rate": rate,
                },
                "by_concept_level": by_level,
            },
        )
