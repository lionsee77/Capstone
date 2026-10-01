"""Case A: adapt incoming source categories into taxonomy, after lionel's
saturation metrics run confirms is new "ADD_CANDIDATE" and "REVIEW_AMBIGUOUS"
that has been resolved as new

reuses saturation's exact/lexical/semantic matcher one level up: source
categories against baseline categories, instead of tags against tags. Then,
under whichever baseline category a source category matched, does the same
thing one level back down: source specialities against just the baseline
specialities that belong to that one matched category. A resolved placement
therefore always carries both a category and a speciality, the way the
classification output contract (CLASSIFICATION_OUTPUT_CONTRACT.md) requires.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..datasets.base import LoadedDataset, TaxonomyDataset
from ..models import MetricResult
from .base import Metric
from .saturation import SemanticMatcher, _compare_level, _KnownPool, _Parents, _validated_float

NEW_CATEGORY = "NEW_CATEGORY"
NEW_SPECIALITY = "NEW_SPECIALITY"
NO_SOURCE_CATEGORY = "NO_SOURCE_CATEGORY"
INVALID_CATEGORY_NAME = "INVALID_CATEGORY_NAME"
RESOLVED_CATEGORY_ACTIONS = {"SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE", NEW_CATEGORY}


def _tag_source_specialities(
    dataset: TaxonomyDataset, tag_ids: set[Any]
) -> dict[Any, list[dict[str, Any]]]:
    """For each of `tag_ids`, the distinct (speciality, category) pairs it's
    linked to via speciality_tags -> specialities.category_id -> categories.
    link only followed if both speciality and category exists, broken link not treated as real one
    """
    categories_by_id = {row.get("id"): row for row in dataset.categories}
    specialities_by_id = {row.get("id"): row for row in dataset.specialities}

    result: dict[Any, list[dict[str, Any]]] = {tag_id: [] for tag_id in tag_ids}
    seen_speciality_ids: dict[Any, set[Any]] = {tag_id: set() for tag_id in tag_ids}
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
        speciality_id = speciality.get("id")
        if speciality_id in seen_speciality_ids[tag_id]:
            continue
        seen_speciality_ids[tag_id].add(speciality_id)
        result[tag_id].append({"speciality": speciality, "category": category})
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


def _match_against_pool(
    level: str,
    source_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    baseline: TaxonomyDataset,
    parents: _Parents,
    settings: dict[str, Any],
    semantic_matcher: SemanticMatcher | None,
) -> dict[str, Any]:
    tables = {"services": baseline.services, "categories": baseline.categories, "specialities": baseline.specialities}
    tables[level] = baseline_rows
    pool = _KnownPool()
    pool.add(baseline.name, TaxonomyDataset(name=baseline.name, **tables))
    return _compare_level(level, source_rows, pool, parents, settings, semantic_matcher, seen_in_source={})


def _outcomes_by_incoming_id(comparison: dict[str, Any], new_action: str) -> dict[Any, dict[str, Any]]:
    outcomes: dict[Any, dict[str, Any]] = {}
    for match in (
        comparison["exact_match_details"] + comparison["fuzzy_match_details"] + comparison["semantic_match_details"]
    ):
        outcome = {
            "action": match["recommended_action"],
            "match_type": match["match_type"],
            "score": match.get("score"),
            "semantic_score": match.get("semantic_score"),
            "target_id": match["baseline_id"],
            "target_name": match["baseline_name"],
        }
        for incoming_id in match["incoming_ids"]:
            outcomes[incoming_id] = outcome
    for ambiguous in comparison["ambiguous_concepts"]:
        if ambiguous["recommended_action"] == "ADD_CANDIDATE":
            outcome = {
                "action": new_action,
                "match_type": None,
                "score": None,
                "semantic_score": None,
                "target_id": None,
                "target_name": ambiguous["incoming_name"],
            }
        else:
            outcome = {
                "action": "REVIEW_AMBIGUOUS",
                "match_type": None,
                "score": None,
                "semantic_score": None,
                "target_id": None,
                "target_name": None,
                "closest_baseline_name": ambiguous.get("closest_baseline_name"),
                "closest_baseline_score": ambiguous.get("closest_baseline_score"),
                # The semantic near-miss is often the more useful hint than
                # the lexical one - "Built Environment" is pure coincidental
                # letter overlap with "Profit Improvement" (57.1 lexical) but
                # genuinely close in meaning to "Construction & Engineering"
                # (0.5352 semantic) - both are worth keeping, not just lexical.
                "closest_semantic_baseline_name": ambiguous.get("closest_semantic_baseline_name"),
                "closest_semantic_score": ambiguous.get("closest_semantic_score"),
            }
        for incoming_id in ambiguous["incoming_ids"]:
            outcomes[incoming_id] = outcome
    return outcomes


class CategoryAdaptationMetric(Metric):
    id = "category_adaptation"
    description = (
        "places an incoming source's own categories and specialities onto the taxonomy for tags "
        "a saturation run has already flagged as new. Reuses saturation's exact/lexical/semantic "
        "matcher one level up for categories, then one level back down for specialities within "
        "whichever category matched."
    )
    default_params = {
        "baseline": None,
        "incoming": None,
        "new_tag_ids": None,
        "threshold": 90.0,
        "semantic_model": None,
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

        settings = {
            "threshold": threshold,
            "semantic_threshold": semantic_threshold,
            "add_candidate_lexical_threshold": add_candidate_lexical_threshold,
            "add_candidate_semantic_threshold": add_candidate_semantic_threshold,
            "cross_level": False,
            "semantic_threshold_by_level": {},
        }
        parents = _Parents(incoming)

        source_specialities_by_tag = _tag_source_specialities(incoming, new_tag_ids)

        
        referenced_categories: dict[Any, dict[str, Any]] = {}
        for entries in source_specialities_by_tag.values():
            for entry in entries:
                referenced_categories[entry["category"].get("id")] = entry["category"]
        source_category_rows = list(referenced_categories.values())

        """ step 1: categories, pool seeded with just baseline, category vs category
       """
        category_comparison = _match_against_pool(
            "categories", source_category_rows, baseline.categories, baseline, parents, settings,
            semantic_matcher,
        )
        category_outcomes = _outcomes_by_incoming_id(category_comparison, NEW_CATEGORY)

        baseline_categories_by_id = {row.get("id"): row for row in baseline.categories}
        baseline_services_by_id = {row.get("id"): row.get("name") for row in baseline.services}
        matched_baseline_rows = [
            baseline_categories_by_id[outcome["target_id"]]
            for outcome in category_outcomes.values()
            if outcome["action"] in ("SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE")
            and baseline_categories_by_id.get(outcome["target_id"]) is not None
        ]

        """step 2: specialities, for categories that are matched or proposed, a source
        speciality is never matched against a baseline speciality that belongs to a different category.
        """
        speciality_outcomes: dict[Any, dict[str, Any]] = {}
        specialities_by_category: dict[Any, dict[Any, dict[str, Any]]] = {}
        for entries in source_specialities_by_tag.values():
            for entry in entries:
                category_id = entry["category"].get("id")
                specialities_by_category.setdefault(category_id, {})[entry["speciality"].get("id")] = entry[
                    "speciality"
                ]

        for category_id, source_specialities_by_id in specialities_by_category.items():
            category_outcome = category_outcomes.get(category_id)
            if category_outcome is None or category_outcome["action"] not in RESOLVED_CATEGORY_ACTIONS:
                continue  # category itself unresolved - nothing to check specialities against
            source_specialities = list(source_specialities_by_id.values())
            if category_outcome["action"] == NEW_CATEGORY:
                baseline_specialities_here: list[dict[str, Any]] = []  # brand new category, nothing to match yet
            else:
                target_category_id = category_outcome["target_id"]
                baseline_specialities_here = [
                    row for row in baseline.specialities if row.get("category_id") == target_category_id
                ]
            speciality_comparison = _match_against_pool(
                "specialities", source_specialities, baseline_specialities_here, baseline, parents, settings,
                semantic_matcher,
            )
            speciality_outcomes.update(_outcomes_by_incoming_id(speciality_comparison, NEW_SPECIALITY))

        for invalid in category_comparison["skipped_invalid"]:
            category_outcomes[invalid["incoming_id"]] = {
                "action": INVALID_CATEGORY_NAME, "target_id": None, "target_name": None,
                "match_type": None, "score": None, "semantic_score": None,
            }

        placements: list[dict[str, Any]] = []
        for tag_id, entries in source_specialities_by_tag.items():
            tag_name = tags_by_id[tag_id].get("name")
            if not entries:
                placements.append(
                    {"tag_id": tag_id, "tag_name": tag_name, "action": NO_SOURCE_CATEGORY, "needs_review": True}
                )
                continue
            seen_unresolved_category_ids: set[Any] = set()
            for entry in entries:
                category, speciality = entry["category"], entry["speciality"]
                category_outcome = category_outcomes[category.get("id")]
                if category_outcome["action"] not in RESOLVED_CATEGORY_ACTIONS:
                    # The category itself didn't resolve, so which speciality
                    # it came through changes nothing about this placement -
                    # several specialities under the same unresolved category
                    # would otherwise produce pure duplicate rows. Collapse
                    # to one row per (tag, category) instead of one per
                    # (tag, speciality) here; resolved categories still get a
                    # row per speciality below, since those genuinely differ.
                    if category.get("id") in seen_unresolved_category_ids:
                        continue
                    seen_unresolved_category_ids.add(category.get("id"))
                placement: dict[str, Any] = {
                    "tag_id": tag_id,
                    "tag_name": tag_name,
                    "source_category_id": category.get("id"),
                    "source_category_name": category.get("name"),
                    "action": category_outcome["action"],
                    "category_match_type": category_outcome["match_type"],
                    "category_score": category_outcome["score"],
                    "category_semantic_score": category_outcome["semantic_score"],
                    "target_category_id": (
                        category_outcome["target_id"]
                        if category_outcome["action"] != NEW_CATEGORY
                        else f"{incoming.name}:{category.get('id')}"
                    ),
                    "target_category_name": category_outcome["target_name"],
                    "needs_review": category_outcome["action"] not in ("SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE"),
                }
                if category_outcome["action"] == "REVIEW_AMBIGUOUS":
                    placement["closest_baseline_name"] = category_outcome.get("closest_baseline_name")
                    placement["closest_baseline_score"] = category_outcome.get("closest_baseline_score")
                    placement["closest_semantic_baseline_name"] = category_outcome.get(
                        "closest_semantic_baseline_name"
                    )
                    placement["closest_semantic_score"] = category_outcome.get("closest_semantic_score")
                if category_outcome["action"] in ("SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE"):
                    # A matched category already has a real service in the
                    # baseline - look it up, don't guess.
                    placement["target_service_id"] = baseline_categories_by_id[category_outcome["target_id"]].get(
                        "service_id"
                    )
                    placement["target_service_name"] = baseline_services_by_id.get(placement["target_service_id"])
                elif category_outcome["action"] == NEW_CATEGORY:
                    placement["target_service_id"] = _assign_service(matched_baseline_rows)
                    placement["target_service_name"] = baseline_services_by_id.get(placement["target_service_id"])

                if category_outcome["action"] in RESOLVED_CATEGORY_ACTIONS:
                    # The speciality genuinely distinguishes placements once
                    # the category resolves - unlike the unresolved case
                    # above, keep one row per (tag, speciality) here.
                    placement["source_speciality_id"] = speciality.get("id")
                    placement["source_speciality_name"] = speciality.get("name")
                    speciality_outcome = speciality_outcomes.get(speciality.get("id"))
                    if speciality_outcome is not None:
                        placement["speciality_action"] = speciality_outcome["action"]
                        placement["speciality_match_type"] = speciality_outcome["match_type"]
                        placement["speciality_score"] = speciality_outcome["score"]
                        placement["speciality_semantic_score"] = speciality_outcome["semantic_score"]
                        target_speciality_id = speciality_outcome["target_id"]
                        placement["target_speciality_id"] = (
                            target_speciality_id
                            if speciality_outcome["action"] != NEW_SPECIALITY
                            else f"{incoming.name}:{speciality.get('id')}"
                        )
                        placement["target_speciality_name"] = speciality_outcome["target_name"]
                        if speciality_outcome["action"] not in (
                            "SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE", NEW_SPECIALITY,
                        ):
                            placement["needs_review"] = True

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
