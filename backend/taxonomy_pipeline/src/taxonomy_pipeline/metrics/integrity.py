"""Integrity: structural soundness of one taxonomy - exact/fuzzy duplicate
names, orphaned rows, and dead ends across categories -> specialities ->
tags. Reuses the same method that found the known issues in Ankit's original
taxonomy (2 exact-dup category clusters, 17 in specialities, 27/110
dead-end categories, etc.) so results are comparable to that baseline.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz, process

from ..datasets.base import LoadedDataset
from ..models import MetricResult
from .base import Metric

_DASH_VARIANTS = re.compile(r"[‐‑‒–—―]")


# Normalize names consistently even when trailing punctuation is separated by whitespace.
def normalize(name: str | None) -> str:
    """Duplicated from (not imported from) `backend/audit/taxonomy_audit.py`'s
    `normalized()`, step for step - that module pulls in `mysql.connector` as
    a live-DB dependency this pipeline deliberately doesn't take on, but the
    steps must still match exactly or duplicate counts stop being comparable
    to that baseline, which is this module's whole point.
    """
    value = unicodedata.normalize("NFKC", name or "").strip().casefold()
    value = _DASH_VARIANTS.sub("-", value)
    value = re.sub(r"\s+", " ", value)
    return value.rstrip(" .,;:!?")


def _exact_duplicate_clusters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        key = normalize(row.get("name"))
        if key:
            by_name[key].append(row.get("id"))
    return [{"name": name, "ids": ids} for name, ids in by_name.items() if len(ids) > 1]


def _fuzzy_duplicate_pairs(rows: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    """O(n^2) via rapidfuzz's vectorized cdist - fine up to a few thousand
    rows (our largest tier today is ~2,400 tags); revisit with blocking or
    an ANN index if a tier grows past ~10k.
    """
    named = [(row.get("id"), normalize(row.get("name"))) for row in rows if row.get("name")]
    names = [name for _, name in named]
    if len(names) < 2:
        return []
    matrix = process.cdist(names, names, scorer=fuzz.ratio)
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            score = matrix[i][j]
            if score >= threshold and names[i] != names[j]:
                pairs.append(
                    {
                        "a": {"id": named[i][0], "name": named[i][1]},
                        "b": {"id": named[j][0], "name": named[j][1]},
                        "score": round(float(score), 1),
                    }
                )
    pairs.sort(key=lambda p: -p["score"])
    return pairs


class IntegrityMetric(Metric):
    id = "integrity"
    description = (
        "Exact/fuzzy duplicate names, orphaned rows, and dead-end nodes across "
        "categories -> specialities -> tags for one dataset."
    )
    default_params = {"dataset": None, "fuzzy_threshold": 85.0}

    def run(self, datasets: dict[str, LoadedDataset], params: dict[str, Any]) -> MetricResult:
        p = self.resolve_params(params)
        dataset_name = p.get("dataset")
        resolved = self.require_datasets(datasets, {"dataset": dataset_name})
        if isinstance(resolved, MetricResult):
            return resolved
        ds = resolved["dataset"]
        threshold = float(p["fuzzy_threshold"])

        category_ids = {row.get("id") for row in ds.categories}
        speciality_ids = {row.get("id") for row in ds.specialities}
        tag_ids = {row.get("id") for row in ds.tags}
        service_ids = {row.get("id") for row in ds.services}

        specialities_per_category: dict[Any, int] = defaultdict(int)
        for row in ds.specialities:
            specialities_per_category[row.get("category_id")] += 1

        tags_per_speciality: dict[Any, int] = defaultdict(int)
        referenced_tag_ids: set[Any] = set()
        dangling_links = []
        for link in ds.speciality_tags:
            sid, tid = link.get("speciality_id"), link.get("tag_id")
            if sid in speciality_ids and tid in tag_ids:
                tags_per_speciality[sid] += 1
                referenced_tag_ids.add(tid)
            else:
                dangling_links.append(link)

        orphan_specialities = [r for r in ds.specialities if r.get("category_id") not in category_ids]
        # A dataset with no services table can't be checked for category ->
        # service orphans at all - report that as skipped (None), not as an
        # empty "all clean" list. A services table that exists but is empty
        # is still checked: every category pointing at a service is an orphan.
        skipped_checks: dict[str, str] = {}
        orphan_categories: list[dict[str, Any]] | None = None
        if ds.has_table("services"):
            orphan_categories = [r for r in ds.categories if r.get("service_id") not in service_ids]
        else:
            skipped_checks["categories_without_service"] = "no services table loaded for this dataset"
        orphan_tags = [r for r in ds.tags if r.get("id") not in referenced_tag_ids]

        dead_end_categories = [
            r for r in ds.categories if specialities_per_category.get(r.get("id"), 0) == 0
        ]
        dead_end_specialities = [
            r for r in ds.specialities if tags_per_speciality.get(r.get("id"), 0) == 0
        ]

        exact = {
            "categories": _exact_duplicate_clusters(ds.categories),
            "specialities": _exact_duplicate_clusters(ds.specialities),
            "tags": _exact_duplicate_clusters(ds.tags),
        }
        details = {
            "exact_duplicates": exact,
            "fuzzy_duplicates": {
                "threshold": threshold,
                "categories": _fuzzy_duplicate_pairs(ds.categories, threshold),
                "specialities": _fuzzy_duplicate_pairs(ds.specialities, threshold),
                "tags": _fuzzy_duplicate_pairs(ds.tags, threshold),
            },
            "orphans": {
                "categories_without_service": (
                    [r.get("id") for r in orphan_categories] if orphan_categories is not None else None
                ),
                "specialities_without_category": [r.get("id") for r in orphan_specialities],
                "tags_unused_by_any_speciality": [r.get("id") for r in orphan_tags],
                "dangling_speciality_tag_links": len(dangling_links),
            },
            "dead_ends": {
                "categories_with_zero_specialities": [r.get("id") for r in dead_end_categories],
                "specialities_with_zero_tags": [r.get("id") for r in dead_end_specialities],
                "category_dead_end_rate": (
                    len(dead_end_categories) / len(ds.categories) if ds.categories else None
                ),
                "speciality_dead_end_rate": (
                    len(dead_end_specialities) / len(ds.specialities) if ds.specialities else None
                ),
            },
            "skipped_checks": skipped_checks,
        }

        exact_dup_rows = sum(len(c["ids"]) for tier in exact.values() for c in tier)
        summary = (
            f"{dataset_name}: {exact_dup_rows} rows in exact-duplicate clusters, "
            f"{len(orphan_specialities)} orphan specialities, {len(orphan_tags)} unused tags, "
            f"{len(dead_end_categories)} dead-end categories, "
            f"{len(dead_end_specialities)} dead-end specialities."
        )
        if skipped_checks:
            summary += f" Skipped: {', '.join(skipped_checks)}."
        return MetricResult(metric_id=self.id, status="ok", summary=summary, details=details)
