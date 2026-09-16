"""Source-by-source taxonomy saturation through concept novelty.

For every named category, speciality, and tag in an incoming taxonomy, find
the closest concept at the same level in a baseline taxonomy. Exact normalized
names are treated as represented; otherwise RapidFuzz supplies a lexical
candidate. Concepts below the configured threshold are *candidate new*, not
automatically confirmed additions: a reviewer decides whether they are truly
new, out of scope, or semantic equivalents with different names.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from rapidfuzz import fuzz, process

from ..datasets.base import LoadedDataset, TaxonomyDataset
from ..models import MetricResult
from .base import Metric
from .integrity import normalize

CONCEPT_LEVELS = ("categories", "specialities", "tags")


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
    baseline_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]], threshold: float
) -> dict[str, Any]:
    baseline = _unique_named_rows(baseline_rows)
    incoming = _unique_named_rows(incoming_rows)
    baseline_by_name = {normalize(row["name"]): row for row in baseline}
    baseline_names = list(baseline_by_name)

    exact_matches: list[dict[str, Any]] = []
    fuzzy_matches: list[dict[str, Any]] = []
    candidate_new: list[dict[str, Any]] = []

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
                }
            )
            continue

        best = process.extractOne(normalized_name, baseline_names, scorer=fuzz.ratio) if baseline_names else None
        if best and best[1] >= threshold:
            matched = baseline_by_name[best[0]]
            fuzzy_matches.append(
                {
                    "incoming_id": row.get("id"),
                    "incoming_name": original_name,
                    "baseline_id": matched.get("id"),
                    "baseline_name": matched.get("name"),
                    "score": round(float(best[1]), 1),
                }
            )
        else:
            candidate_new.append(
                {
                    "incoming_id": row.get("id"),
                    "incoming_name": original_name,
                    "closest_baseline_name": best[0] if best else None,
                    "closest_baseline_score": round(float(best[1]), 1) if best else None,
                }
            )

    reviewed = len(incoming)
    new_count = len(candidate_new)
    return {
        "relevant_concepts_reviewed": reviewed,
        "exact_matches": len(exact_matches),
        "fuzzy_candidate_matches": len(fuzzy_matches),
        "candidate_new": new_count,
        "new_concept_rate": round(new_count / reviewed, 3) if reviewed else None,
        "exact_match_details": exact_matches,
        "fuzzy_match_details": fuzzy_matches,
        "candidate_new_concepts": candidate_new,
    }


class SaturationMetric(Metric):
    id = "saturation"
    description = (
        "Incoming-source novelty against a baseline taxonomy. Uses normalized exact matches and "
        "RapidFuzz lexical matches; unmatched concepts are candidate new concepts for review."
    )
    default_params = {"baseline": None, "incoming": None, "threshold": 90.0}

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
        except (TypeError, ValueError) as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))

        baseline, incoming = resolved["baseline"], resolved["incoming"]
        by_level = {
            level: _compare_level(getattr(baseline, level), getattr(incoming, level), threshold)
            for level in CONCEPT_LEVELS
        }
        reviewed = sum(result["relevant_concepts_reviewed"] for result in by_level.values())
        candidate_new = sum(result["candidate_new"] for result in by_level.values())
        rate = round(candidate_new / reviewed, 3) if reviewed else None
        summary = (
            f"{incoming.name} against {baseline.name}: {candidate_new}/{reviewed} "
            f"({rate:.1%}) candidate new concepts require review."
            if rate is not None
            else f"{incoming.name} has no named categories, specialities, or tags to compare."
        )
        return MetricResult(
            metric_id=self.id,
            summary=summary,
            details={
                "baseline": baseline.name,
                "incoming": incoming.name,
                "threshold": threshold,
                "overall": {
                    "relevant_concepts_reviewed": reviewed,
                    "candidate_new": candidate_new,
                    "new_concept_rate": rate,
                },
                "by_concept_level": by_level,
            },
        )
