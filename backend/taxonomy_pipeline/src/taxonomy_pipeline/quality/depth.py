"""Depth: branching factor of the hierarchy - how many specialities sit under
each category, and how many tags are linked to each speciality.
"""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from ..models import Measure, MetricResult
from ..taxonomy.base import LoadedDataset, TaxonomyDataset
from .base import Metric


def speciality_tag_counts(ds: TaxonomyDataset) -> dict[Any, int]:
    """Tags linked to each speciality, by speciality ID, counting zero for an unlinked one.

    Both ends of a link must resolve, matching integrity.py's dangling-link
    check - otherwise a link to a nonexistent tag still counts as a real tag
    here, inflating the branching-factor baseline. Public so integrate can split
    the same count into existing vs new specialities.
    """
    speciality_ids = {row.get("id") for row in ds.specialities}
    tag_ids = {row.get("id") for row in ds.tags}
    linked = Counter(
        link.get("speciality_id")
        for link in ds.speciality_tags
        if link.get("speciality_id") in speciality_ids and link.get("tag_id") in tag_ids
    )
    return {row.get("id"): linked.get(row.get("id"), 0) for row in ds.specialities}


def _stats(counts: list[int]) -> dict[str, Any]:
    if not counts:
        return {"min": 0, "max": 0, "mean": 0, "median": 0, "n": 0}
    return {
        "min": min(counts),
        "max": max(counts),
        "mean": round(statistics.mean(counts), 2),
        "median": statistics.median(counts),
        "n": len(counts),
    }


class DepthMetric(Metric):
    id = "depth"
    subject_param = "dataset"
    description = "Category -> speciality and speciality -> tag branching factor."
    default_params = {"dataset": None}

    def measures(self, result: MetricResult) -> list[Measure]:
        """Tags per speciality, higher is better: a speciality with more tags gives matching more to go on.
        Specialities per category has no better direction (adding categories with few specialities lowers
        it), and the row counts give the size the means are over."""
        to_speciality = result.details.get("category_to_speciality", {})
        to_tag = result.details.get("speciality_to_tag", {})
        return [
            Measure(key="speciality_to_tag.mean", label="Tags per speciality (mean)",
                    value=to_tag.get("mean"), better="higher"),
            Measure(key="speciality_to_tag.median", label="Tags per speciality (median)",
                    value=to_tag.get("median"), better="higher"),
            Measure(key="category_to_speciality.mean", label="Specialities per category (mean)",
                    value=to_speciality.get("mean")),
            Measure(key="category_to_speciality.median", label="Specialities per category (median)",
                    value=to_speciality.get("median")),
            Measure(key="category_to_speciality.n", label="Categories", value=to_speciality.get("n")),
            Measure(key="speciality_to_tag.n", label="Specialities", value=to_tag.get("n")),
        ]

    def run(self, datasets: dict[str, LoadedDataset], params: dict[str, Any]) -> MetricResult:
        p = self.resolve_params(params)
        dataset_name = p.get("dataset")
        resolved = self.require_datasets(datasets, {"dataset": dataset_name})
        if isinstance(resolved, MetricResult):
            return resolved
        ds = resolved["dataset"]

        specialities_per_category = Counter(row.get("category_id") for row in ds.specialities)
        category_counts = [specialities_per_category.get(row.get("id"), 0) for row in ds.categories]

        speciality_counts = list(speciality_tag_counts(ds).values())

        details = {
            "category_to_speciality": _stats(category_counts),
            "speciality_to_tag": _stats(speciality_counts),
        }
        summary = (
            f"{dataset_name}: category->speciality mean "
            f"{details['category_to_speciality']['mean']} "
            f"(n={details['category_to_speciality']['n']}), "
            f"speciality->tag mean {details['speciality_to_tag']['mean']} "
            f"(n={details['speciality_to_tag']['n']})."
        )
        return MetricResult(metric_id=self.id, status="ok", summary=summary, details=details)
