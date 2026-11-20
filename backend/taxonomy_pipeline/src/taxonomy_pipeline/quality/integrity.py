"""Integrity: structural soundness of one taxonomy - exact/fuzzy duplicate
names, orphaned rows, and dead ends across categories -> specialities ->
tags. Reuses the same method that found the known issues in Ankit's original
taxonomy (2 exact-dup category clusters, 17 in specialities, 27/110
dead-end categories, etc.) so results are comparable to that baseline.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz, process

from ..matching.names import normalize
from ..models import Measure, MetricResult, Unit
from ..taxonomy.base import LoadedDataset
from .base import Metric


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


_LEVELS = (("categories", "Categories", "category"), ("specialities", "Specialities", "speciality"),
           ("tags", "Tags", "tag"))


def _cluster_rows(clusters: list[dict[str, Any]] | None) -> int | None:
    """Rows in a level's exact-duplicate clusters; None for a level the result has no entry for."""
    return None if clusters is None else sum(len(cluster["ids"]) for cluster in clusters)


# (details section, key, label, unit) for each orphan and dead-end measure. A "number" entry is
# a list of defective rows, measured by its length, except `dangling_speciality_tag_links`,
# which the result stores as a count already; a "rate" is stored as the fraction.
_DEFECTS = (
    ("orphans", "categories_without_service", "Categories without a service", "number"),
    ("orphans", "specialities_without_category", "Specialities without a category", "number"),
    ("orphans", "tags_unused_by_any_speciality", "Tags no speciality uses", "number"),
    ("orphans", "dangling_speciality_tag_links", "Dangling speciality-tag links", "number"),
    ("dead_ends", "categories_with_zero_specialities", "Dead-end categories", "number"),
    ("dead_ends", "specialities_with_zero_tags", "Dead-end specialities", "number"),
    ("dead_ends", "category_dead_end_rate", "Dead-end category rate", "rate"),
    ("dead_ends", "speciality_dead_end_rate", "Dead-end speciality rate", "rate"),
)


def _defect(key: str, label: str, value: Any, unit: Unit = "number") -> Measure:
    """A defect measure, lower is better. A list of defective rows counts as its length; None is a
    check the run skipped (recorded as None, not an empty list)."""
    return Measure(key=key, label=label, value=len(value) if isinstance(value, list) else value,
                   better="lower", unit=unit)


class IntegrityMetric(Metric):
    id = "integrity"
    subject_param = "dataset"
    description = (
        "Exact/fuzzy duplicate names, orphaned rows, and dead-end nodes across "
        "categories -> specialities -> tags for one dataset."
    )
    default_params = {"dataset": None, "fuzzy_threshold": 85.0}

    def measures(self, result: MetricResult) -> list[Measure]:
        """Every defect count, lower is better. Counts, not rates, so they grow with the taxonomy: an
        enrichment that adds rows can add defects too, and that is what a reviewer needs to see (size-independent
        duplication rates are future work). A check the run skipped (no services table) has no value."""
        details = result.details
        exact, fuzzy = details.get("exact_duplicates") or {}, details.get("fuzzy_duplicates") or {}
        measures = [
            _defect(f"exact_duplicate_rows.{level}", f"{label} in exact-duplicate clusters",
                    _cluster_rows(exact.get(level)))
            for level, label, _ in _LEVELS
        ]
        measures += [
            _defect(f"fuzzy_duplicate_pairs.{level}", f"Fuzzy-duplicate {singular} pairs", fuzzy.get(level))
            for level, _, singular in _LEVELS
        ]
        return [*measures, *(
            _defect(f"{section}.{key}", label, (details.get(section) or {}).get(key), unit)
            for section, key, label, unit in _DEFECTS
        )]

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
