"""Breadth: sector coverage against an external reference - for each category
name in a `reference` dataset (e.g. the SkillsFuture sectors), is there a
matching category in the `target` dataset under test? This compares one
taxonomy against a benchmark; it does not score a dataset in isolation.
"""
from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz, process

from ..datasets.base import TaxonomyDataset
from ..models import MetricResult
from .base import Metric
from .integrity import normalize


class BreadthMetric(Metric):
    id = "breadth"
    description = (
        "Fraction of a reference dataset's category names that have a matching "
        "category in the target dataset (fuzzy name match)."
    )
    default_params = {"target": None, "reference": None, "threshold": 75.0}

    def run(self, datasets: dict[str, TaxonomyDataset], params: dict[str, Any]) -> MetricResult:
        p = self.resolve_params(params)
        target_name, reference_name = p.get("target"), p.get("reference")
        resolved = self.require_datasets(datasets, {"target": target_name, "reference": reference_name})
        if isinstance(resolved, MetricResult):
            return resolved
        threshold = float(p["threshold"])
        target, reference = resolved["target"], resolved["reference"]

        target_names = [normalize(row.get("name")) for row in target.categories if row.get("name")]
        reference_rows = [row for row in reference.categories if row.get("name")]

        matched, unmatched = [], []
        for row in reference_rows:
            name = normalize(row.get("name"))
            best = process.extractOne(name, target_names, scorer=fuzz.ratio) if target_names else None
            if best and best[1] >= threshold:
                matched.append(
                    {"reference": row.get("name"), "matched_to": best[0], "score": round(best[1], 1)}
                )
            else:
                unmatched.append(row.get("name"))

        coverage = len(matched) / len(reference_rows) if reference_rows else None
        details = {
            "target": target_name,
            "reference": reference_name,
            "threshold": threshold,
            "coverage": round(coverage, 3) if coverage is not None else None,
            "matched": matched,
            "unmatched_reference_categories": unmatched,
        }
        summary = (
            f"{target_name} covers {len(matched)}/{len(reference_rows)} "
            f"({coverage:.0%}) of {reference_name}'s categories."
            if coverage is not None
            else f"no categories in {reference_name} to compare."
        )
        return MetricResult(metric_id=self.id, status="ok", summary=summary, details=details)
