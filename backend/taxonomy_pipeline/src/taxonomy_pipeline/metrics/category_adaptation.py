"""Case A: adapt incoming source categories into taxonomy, after lionel's 
saturation metrics run confirms is new "ADD_CANDIDATE" and "REVIEW_AMBIGUOUS"
that has been resolved as new

reuses saturation's exact/lexical/semantic matcher one level up: source
categories against baseline categories, instead of tags against tags.

"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..datasets.base import LoadedDataset, TaxonomyDataset
from ..models import MetricResult
from .base import Metric
from .saturation import SemanticMatcher, _compare_level, _KnownPool, _Parents, _validated_float

NEW_CATEGORY = "NEW_CATEGORY"
NO_SOURCE_CATEGORY = "NO_SOURCE_CATEGORY"
INVALID_CATEGORY_NAME = "INVALID_CATEGORY_NAME"


def _tag_source_categories(
    dataset: TaxonomyDataset, tag_ids: set[Any]
) -> dict[Any, list[dict[str, Any]]]:
    """For each of `tag_ids`, the distinct source category rows it's linked
    to via speciality_tags -> specialities.category_id -> categories.

    link is only followed if both the speciality and the category actually 
    exist so a broken link doesn't get treated as a real placement.
    """
    categories_by_id = {row.get("id"): row for row in dataset.categories}
    specialities_by_id = {row.get("id"): row for row in dataset.specialities}

    result: dict[Any, list[dict[str, Any]]] = {tag_id: [] for tag_id in tag_ids}
    seen_category_ids: dict[Any, set[Any]] = {tag_id: set() for tag_id in tag_ids}
    for link in dataset.speciality_tags:
        tag_id = link.get("tag_id")
        if tag_id not in tag_ids:
            continue
        speciality = specialities_by_id.get(link.get("speciality_id"))
        if speciality is None:
            continue
        category = categories_by_id.get(speciality.get("category_id"))
        if category is None:
            continue
        category_id = category.get("id")
        if category_id in seen_category_ids[tag_id]:
            continue
        seen_category_ids[tag_id].add(category_id)
        result[tag_id].append(category)
    return result


def _assign_service(matched_baseline_categories: list[dict[str, Any]]) -> Any:
    """look at all the other source categories in this same run that did successfully 
    match an existing baseline category. 
    Each of those matched baseline categories already has a real service_id. 
    Take whichever service_id shows up most often among them, and use that as the new category's guess.
    """
    service_ids = [row.get("service_id") for row in matched_baseline_categories if row.get("service_id") is not None]
    if not service_ids:
        return None
    return Counter(service_ids).most_common(1)[0][0]


class CategoryAdaptationMetric(Metric):
    id = "category_adaptation"
    description = (
        "places an incoming source's own categories onto the taxonomy for tags "
        "a saturation run has already flagged as new. Reuses saturation's exact/lexical/semantic "
        "matcher one level up (category vs category, not tag vs tag)."
    )
    default_params = {
        "baseline": None,
        "incoming": None,
        "new_tag_ids": None,
        "threshold": 90.0,
        "semantic_model": None,
        # Lower than saturation.py's tag-level default (0.7): category names
        # are fewer and more varied in phrasing than tag names, so the same
        # cutoff rejects good matches. A real run against SkillsFuture found
        # 7 correct matches (e.g. "Accountancy" -> "accounting finance" at
        # 0.6573, "Security" -> "cybersecurity" at 0.6403) sitting between
        # 0.63-0.694, while every genuine new category scored below 0.46 -
        # 0.60 separates them cleanly. Re-tune if a future source's category
        # names behave differently.
        "semantic_threshold": 0.6,
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
        baseline, incoming = resolved["baseline"], resolved["incoming"]

        new_tag_ids = p["new_tag_ids"]
        if new_tag_ids is None:
            return MetricResult(
                metric_id=self.id,
                status="error",
                summary=(
                    "new_tag_ids is required: the confirmed-new tag ids from a saturation run's "
                    "ADD_CANDIDATE outcomes (plus any REVIEW_AMBIGUOUS tags a reviewer resolved as "
                    "new). "
                ),
            )
        new_tag_ids = set(new_tag_ids)
        if not new_tag_ids:
            return MetricResult(
                metric_id=self.id,
                status="ok",
                summary=f"{incoming.name}: no new tags to place.",
                details={"baseline": baseline.name, "incoming": incoming.name, "placements": []},
            )

        tags_by_id = {row.get("id"): row for row in incoming.tags}
        unknown = new_tag_ids - tags_by_id.keys()
        if unknown:
            return MetricResult(
                metric_id=self.id,
                status="error",
                summary=f"new_tag_ids not found in {incoming.name}.tags: {sorted(unknown, key=str)}",
            )

        try:
            threshold = _validated_float(p, "threshold", 0, 100)
            semantic_threshold = _validated_float(p, "semantic_threshold", -1, 1)
            add_candidate_lexical_threshold = _validated_float(p, "add_candidate_lexical_threshold", 0, 100)
            add_candidate_semantic_threshold = _validated_float(p, "add_candidate_semantic_threshold", -1, 1)
        except (TypeError, ValueError) as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        try:
            semantic_matcher = SemanticMatcher(str(p["semantic_model"])) if p["semantic_model"] else None
        except ValueError as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))

        source_categories_by_tag = _tag_source_categories(incoming, new_tag_ids)

        # The distinct source categories actually referenced by a new tag -
        # not incoming.categories wholesale - since Case A only cares about
        # categories actually in play for this run's new tags.
        referenced: dict[Any, dict[str, Any]] = {}
        for categories in source_categories_by_tag.values():
            for category in categories:
                referenced[category.get("id")] = category
        source_category_rows = list(referenced.values())

        # Reuses saturation's own matcher machinery: a pool seeded with just
        # the baseline (no earlier incoming sources - Case A only ever
        # compares one source's categories against the accepted baseline).
        # cross_level=False keeps this to category-vs-category only.
        pool = _KnownPool()
        pool.add(baseline.name, baseline)
        parents = _Parents(incoming)
        settings = {
            "threshold": threshold,
            "semantic_threshold": semantic_threshold,
            "add_candidate_lexical_threshold": add_candidate_lexical_threshold,
            "add_candidate_semantic_threshold": add_candidate_semantic_threshold,
            "cross_level": False,
            "semantic_threshold_by_level": {},
        }
        category_comparison = _compare_level(
            "categories", source_category_rows, pool, parents, settings, semantic_matcher, seen_in_source={}
        )

        baseline_categories_by_id = {row.get("id"): row for row in baseline.categories}
        matched_baseline_rows: list[dict[str, Any]] = []
        # Keyed by source category id (every match/ambiguous entry now
        # carries `incoming_ids`, covering every id in its name-grouped
        # cluster) - simpler and safer than the old name-based keying, which
        # broke when two source categories shared a normalized name.
        placement_by_source_category_id: dict[Any, dict[str, Any]] = {}

        for match in (
            category_comparison["exact_match_details"]
            + category_comparison["fuzzy_match_details"]
            + category_comparison["semantic_match_details"]
        ):
            baseline_row = baseline_categories_by_id.get(match["baseline_id"])
            if baseline_row is not None:
                matched_baseline_rows.append(baseline_row)
            placement = {
                "action": match["recommended_action"],
                "match_type": match["match_type"],
                "score": match.get("score", match.get("semantic_score")),
                "target_category_id": match["baseline_id"],
                "target_category_name": match["baseline_name"],
                "needs_review": False,
            }
            for source_category_id in match["incoming_ids"]:
                placement_by_source_category_id[source_category_id] = placement

        # Known concept, but the source files it under a different service
        # than the baseline does (e.g. its own service parent doesn't cover
        # what the matched baseline category is known under) - genuinely
        # worth a human's attention, not a plain skip.
        for placement_entry in category_comparison["new_placement_details"]:
            for source_category_id in placement_entry["incoming_ids"]:
                placement_by_source_category_id[source_category_id] = {
                    "action": "REVIEW_NEW_PLACEMENT",
                    "match_type": placement_entry["match_type"],
                    "score": placement_entry.get("score", placement_entry.get("semantic_score")),
                    "target_category_id": placement_entry["baseline_id"],
                    "target_category_name": placement_entry["baseline_name"],
                    "existing_parents": placement_entry.get("existing_parents"),
                    "unmatched_incoming_parents": placement_entry.get("unmatched_incoming_parents"),
                    "needs_review": True,
                }

        # cross_level=False means this should never actually fire; handled
        # defensively rather than silently dropping a category outright.
        for other_level in category_comparison["other_level_match_details"]:
            for source_category_id in other_level["incoming_ids"]:
                placement_by_source_category_id[source_category_id] = {
                    "action": "REVIEW_OTHER_LEVEL",
                    "match_type": other_level.get("match_type"),
                    "target_category_id": None,
                    "target_category_name": None,
                    "needs_review": True,
                }

        for ambiguous in category_comparison["ambiguous_concepts"]:
            if ambiguous["recommended_action"] == "ADD_CANDIDATE":
                placement = {
                    "action": NEW_CATEGORY,
                    "match_type": None,
                    "score": None,
                    "target_category_id": f"{incoming.name}:{ambiguous['incoming_id']}",
                    "target_category_name": ambiguous["incoming_name"],
                    "service_id": _assign_service(matched_baseline_rows),
                    "needs_review": True,
                }
            else:  # REVIEW_AMBIGUOUS - defer to Lionel's ambiguity box, don't decide here.
                placement = {
                    "action": "REVIEW_AMBIGUOUS",
                    "match_type": None,
                    "score": None,
                    "closest_baseline_name": ambiguous.get("closest_baseline_name"),
                    "closest_baseline_score": ambiguous.get("closest_baseline_score"),
                    "target_category_id": None,
                    "target_category_name": None,
                    "needs_review": True,
                }
            for source_category_id in ambiguous["incoming_ids"]:
                placement_by_source_category_id[source_category_id] = placement

       
        for invalid in category_comparison["skipped_invalid"]:
            placement_by_source_category_id[invalid["incoming_id"]] = {
                "action": INVALID_CATEGORY_NAME,
                "target_category_id": None,
                "target_category_name": None,
                "needs_review": True,
            }

        placements: list[dict[str, Any]] = []
        for tag_id, categories in source_categories_by_tag.items():
            tag_name = tags_by_id[tag_id].get("name")
            if not categories:
                placements.append(
                    {"tag_id": tag_id, "tag_name": tag_name, "action": NO_SOURCE_CATEGORY, "needs_review": True}
                )
                continue
            for category in categories:
                placement = dict(placement_by_source_category_id[category.get("id")])
                placement["tag_id"] = tag_id
                placement["tag_name"] = tag_name
                placement["source_category_id"] = category.get("id")
                placement["source_category_name"] = category.get("name")
                placements.append(placement)

        action_counts = Counter(placement["action"] for placement in placements)
        summary = (
            f"{incoming.name}: {len(new_tag_ids)} new tags across {len(source_category_rows)} source "
            f"categories against {baseline.name} - "
            + ", ".join(f"{action}={count}" for action, count in sorted(action_counts.items()))
        )
        return MetricResult(
            metric_id=self.id,
            status="ok",
            summary=summary,
            details={
                "baseline": baseline.name,
                "incoming": incoming.name,
                "category_comparison": category_comparison,
                "placements": placements,
            },
        )
