"""Case A: place the new tags a saturation run flagged (`ADD_CANDIDATE` or
`REVIEW_AMBIGUOUS`, with a parent in their own source) onto the baseline taxonomy.

A source's own levels don't have to mean what the baseline's do. In
SkillsFuture the tag is a skill, its speciality a job role and its category a
sector, while the baseline's specialities are themselves skills. Placing a
tag through its source specialities therefore proposes job titles as
specialities (one row per job role, up to ~100 per skill), so this metric
never uses source specialities as placement targets. It asks what the tag
*is*, the same question Case B asks, in two steps:

1. **Content**: the tag itself is matched against every baseline speciality
   with saturation's exact/lexical/semantic matcher. A confident match is the
   placement - one row, with the category and service that speciality really
   sits under.
2. **Source category fallback**: a tag that matches nothing is placed under
   the baseline category its source category (e.g. SkillsFuture sector)
   matches, as a proposed new speciality named after the tag itself - one row
   per distinct source category. The source specialities linking the tag to
   that category (job roles) are kept as evidence only.
3. **Review candidates**: when the source category only partly matches (any
   REVIEW_* outcome), nothing is decided automatically. The tag is proposed
   under each of the closest few baseline categories instead, every row
   flagged `needs_review`, so merge-preview holds it until a reviewer approves
   one path - rather than emitting an empty record nobody can act on.

A resolved placement always carries both a category and a speciality, as the
classification output contract (CLASSIFICATION_OUTPUT_CONTRACT.md) requires.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
from rapidfuzz import fuzz, process

from ..datasets.base import LoadedDataset, TaxonomyDataset
from ..models import MetricResult
from .base import Metric
from .integrity import normalize
from .saturation import (
    SemanticMatcher,
    _compare_level,
    _is_valid_name,
    _KnownPool,
    _Parents,
    _validated_float,
    match_key,
)

NEW_CATEGORY = "NEW_CATEGORY"
NEW_SPECIALITY = "NEW_SPECIALITY"
NO_SOURCE_CATEGORY = "NO_SOURCE_CATEGORY"
INVALID_CATEGORY_NAME = "INVALID_CATEGORY_NAME"
MATCHED_ACTIONS = {"SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE"}
RESOLVED_CATEGORY_ACTIONS = MATCHED_ACTIONS | {NEW_CATEGORY}
REVIEW_CATEGORY_ACTIONS = {"REVIEW_AMBIGUOUS", "REVIEW_NEW_PLACEMENT", "REVIEW_OTHER_LEVEL"}
# How a placement was reached - see the module docstring.
CONTENT_BASIS = "content"
SOURCE_CATEGORY_BASIS = "source_category"
REVIEW_CANDIDATE_BASIS = "review_candidate"
# Source specialities named on a fallback placement, as evidence for a reviewer.
EVIDENCE_SAMPLE = 3
# Closest baseline categories offered when a source category only partly matches.
REVIEW_CANDIDATES = 3


def _tag_source_categories(dataset: TaxonomyDataset, tag_ids: set[Any]) -> dict[Any, list[dict[str, Any]]]:
    """For each of `tag_ids`, its distinct source categories, each with the
    source specialities (e.g. job roles) that link the tag to it, via
    speciality_tags -> specialities.category_id -> categories. A link is only
    followed if both the speciality and the category exist.
    """
    categories_by_id = {row.get("id"): row for row in dataset.categories}
    specialities_by_id = {row.get("id"): row for row in dataset.specialities}

    grouped: dict[Any, dict[Any, dict[str, Any]]] = {tag_id: {} for tag_id in tag_ids}
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
        entry = grouped[tag_id].setdefault(category.get("id"), {"category": category, "specialities": {}})
        entry["specialities"][speciality.get("id")] = speciality
    return {
        tag_id: [
            {"category": entry["category"],
             "speciality_names": sorted(str(row.get("name")) for row in entry["specialities"].values())}
            for entry in by_category.values()
        ]
        for tag_id, by_category in grouped.items()
    }


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
    # exact/fuzzy/semantic matches are the "skip, reuse this baseline id" case;
    # new_placement/other_level matches still found a similar baseline concept
    # (they carry the same baseline_id/baseline_name/match_type shape via
    # _match_entry) but flag it for review instead - a category whose source
    # service doesn't match the baseline's (REVIEW_NEW_PLACEMENT), or one that
    # matched something at a different taxonomy level (REVIEW_OTHER_LEVEL).
    for match in (
        comparison["exact_match_details"] + comparison["fuzzy_match_details"] + comparison["semantic_match_details"]
        + comparison["new_placement_details"] + comparison["other_level_match_details"]
    ):
        outcome = {
            "action": match["recommended_action"],
            "match_type": match["match_type"],
            "score": match.get("score"),
            "semantic_score": match.get("semantic_score"),
            "target_id": match["baseline_id"],
            "target_name": match["baseline_name"],
        }
        if match["recommended_action"] == "REVIEW_NEW_PLACEMENT":
            outcome["unmatched_incoming_parents"] = [p["name"] for p in match.get("unmatched_incoming_parents", [])]
            outcome["existing_parents"] = match.get("existing_parents", [])
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


def _closest_categories(
    name: Any, categories: list[dict[str, Any]], semantic_matcher: SemanticMatcher | None,
) -> list[tuple[dict[str, Any], float, str]]:
    """The REVIEW_CANDIDATES baseline categories closest to a source category name, as
    (row, score, score_type): by meaning when a model is loaded, else by spelling."""
    named = [row for row in categories if _is_valid_name(row.get("name"))]
    if not named or not _is_valid_name(name):
        return []
    if semantic_matcher:
        scores = semantic_matcher.similarity([normalize(str(name))], [normalize(str(row["name"])) for row in named])[0]
        best = np.argsort(-np.asarray(scores), kind="stable")[:REVIEW_CANDIDATES]
        return [(named[index], round(float(scores[index]), 4), "semantic") for index in best]
    best = process.extract(
        match_key(name), [match_key(row["name"]) for row in named], scorer=fuzz.ratio, limit=REVIEW_CANDIDATES,
    )
    return [(named[index], round(float(score), 1), "lexical") for _, score, index in best]


def _near_miss(outcome: dict[str, Any] | None) -> dict[str, Any]:
    """The closest baseline speciality a tag missed in the content step, for the reason text."""
    if outcome is None or outcome["action"] != "REVIEW_AMBIGUOUS":
        return {}
    return {
        "closest_speciality_name": outcome.get("closest_baseline_name"),
        "closest_speciality_score": outcome.get("closest_baseline_score"),
        "closest_semantic_speciality_name": outcome.get("closest_semantic_baseline_name"),
        "closest_semantic_speciality_score": outcome.get("closest_semantic_score"),
    }


class CategoryAdaptationMetric(Metric):
    id = "category_adaptation"
    description = (
        "places tags a saturation run has already flagged as new: first by matching the tag itself against "
        "every baseline speciality, otherwise as a new speciality under the baseline category its own source "
        "category matches. Source specialities (e.g. SkillsFuture job roles) are evidence, never targets."
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
                    "new_tag_ids is required: the tag ids a saturation run marked ADD_CANDIDATE or "
                    "REVIEW_AMBIGUOUS (see classification/routing.py)."
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
        baseline_categories_by_id = {row.get("id"): row for row in baseline.categories}
        baseline_services_by_id = {row.get("id"): row.get("name") for row in baseline.services}
        tag_rows = [row for row in incoming.tags if row.get("id") in new_tag_ids]

        # Step 1, content: the tag against every baseline speciality. A tag row
        # has no category_id, so the parent check is skipped (no_incoming_parent)
        # and a name match is a plain match, never REVIEW_NEW_PLACEMENT.
        content_comparison = _match_against_pool(
            "specialities", tag_rows, baseline.specialities, baseline, parents, settings, semantic_matcher,
        )
        content_outcomes = _outcomes_by_incoming_id(content_comparison, NEW_SPECIALITY)
        baseline_specialities_by_id = {row.get("id"): row for row in baseline.specialities}
        # A content match only counts when the matched speciality's category and
        # service both exist: the contract needs all three, and a record with a
        # category but no service would fail validation for the whole batch.
        content_matched = {}
        for tag_id, outcome in content_outcomes.items():
            matched_category = baseline_categories_by_id.get(
                baseline_specialities_by_id.get(outcome["target_id"], {}).get("category_id")
            )
            if (
                outcome["action"] in MATCHED_ACTIONS and matched_category is not None
                and baseline_services_by_id.get(matched_category.get("service_id")) is not None
            ):
                content_matched[tag_id] = outcome

        # Step 2, source category fallback for every tag step 1 didn't place.
        fallback_tag_ids = new_tag_ids - content_matched.keys()
        source_categories_by_tag = _tag_source_categories(incoming, fallback_tag_ids)
        referenced_categories = {
            entry["category"].get("id"): entry["category"]
            for entries in source_categories_by_tag.values() for entry in entries
        }
        category_comparison = _match_against_pool(
            "categories", list(referenced_categories.values()), baseline.categories, baseline, parents, settings,
            semantic_matcher,
        )
        category_outcomes = _outcomes_by_incoming_id(category_comparison, NEW_CATEGORY)
        for invalid in category_comparison["skipped_invalid"]:
            category_outcomes[invalid["incoming_id"]] = {
                "action": INVALID_CATEGORY_NAME, "target_id": None, "target_name": None,
                "match_type": None, "score": None, "semantic_score": None,
            }
        # Only categories whose service the baseline knows can be proposed: the
        # contract needs all of service, category and speciality together.
        reviewable_categories = [
            row for row in baseline.categories if baseline_services_by_id.get(row.get("service_id")) is not None
        ]
        review_candidates = {
            category_id: _closest_categories(row.get("name"), reviewable_categories, semantic_matcher)
            for category_id, row in referenced_categories.items()
            if category_outcomes[category_id]["action"] in REVIEW_CATEGORY_ACTIONS
        }
        matched_baseline_rows = [
            baseline_categories_by_id[outcome["target_id"]]
            for outcome in category_outcomes.values()
            if outcome["action"] in MATCHED_ACTIONS and baseline_categories_by_id.get(outcome["target_id"]) is not None
        ]

        placements: list[dict[str, Any]] = []
        for row in tag_rows:
            tag_id, tag_name = row.get("id"), row.get("name")
            if tag_id in content_matched:
                placements.append(self._content_placement(
                    tag_id, tag_name, content_matched[tag_id], baseline_specialities_by_id,
                    baseline_categories_by_id, baseline_services_by_id,
                ))
                continue
            entries = source_categories_by_tag[tag_id]
            near_miss = _near_miss(content_outcomes.get(tag_id))
            if not entries:
                placements.append({
                    "tag_id": tag_id, "tag_name": tag_name, "action": NO_SOURCE_CATEGORY, "needs_review": True,
                    **near_miss,
                })
                continue
            for entry in entries:
                category = entry["category"]
                category_outcome = category_outcomes[category.get("id")]
                placement: dict[str, Any] = {
                    "tag_id": tag_id,
                    "tag_name": tag_name,
                    "placement_basis": SOURCE_CATEGORY_BASIS,
                    "source_category_id": category.get("id"),
                    "source_category_name": category.get("name"),
                    "source_speciality_count": len(entry["speciality_names"]),
                    "source_speciality_sample": entry["speciality_names"][:EVIDENCE_SAMPLE],
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
                    "needs_review": category_outcome["action"] not in MATCHED_ACTIONS,
                    **near_miss,
                }
                if category_outcome["action"] == "REVIEW_AMBIGUOUS":
                    for field in ("closest_baseline_name", "closest_baseline_score",
                                  "closest_semantic_baseline_name", "closest_semantic_score"):
                        placement[field] = category_outcome.get(field)
                elif category_outcome["action"] == "REVIEW_NEW_PLACEMENT":
                    placement["unmatched_incoming_parents"] = category_outcome.get("unmatched_incoming_parents")
                    placement["existing_parents"] = category_outcome.get("existing_parents")
                if category_outcome["action"] in MATCHED_ACTIONS:
                    # A matched category already has a real service in the
                    # baseline - look it up, don't guess.
                    placement["target_service_id"] = baseline_categories_by_id[category_outcome["target_id"]].get(
                        "service_id"
                    )
                elif category_outcome["action"] == NEW_CATEGORY:
                    placement["target_service_id"] = _assign_service(matched_baseline_rows)
                if category_outcome["action"] in RESOLVED_CATEGORY_ACTIONS:
                    placement["target_service_name"] = baseline_services_by_id.get(placement["target_service_id"])
                    # Step 1 already searched every baseline speciality, so
                    # nothing under this category matches the tag either: the
                    # tag itself is the proposed speciality.
                    placement["speciality_action"] = NEW_SPECIALITY
                    placement["target_speciality_id"] = f"{incoming.name}:tag:{tag_id}"
                    placement["target_speciality_name"] = tag_name
                candidates = review_candidates.get(category.get("id")) or []
                if not candidates:
                    placements.append(placement)
                    continue
                for rank, (candidate, score, score_type) in enumerate(candidates, start=1):
                    placements.append({
                        **placement,
                        "placement_basis": REVIEW_CANDIDATE_BASIS,
                        "candidate_rank": rank,
                        "candidate_count": len(candidates),
                        "candidate_score": score,
                        "candidate_score_type": score_type,
                        "candidate_category_id": candidate.get("id"),
                        "candidate_category_name": candidate.get("name"),
                        "candidate_service_id": candidate.get("service_id"),
                        "candidate_service_name": baseline_services_by_id[candidate.get("service_id")],
                        "speciality_action": NEW_SPECIALITY,
                        "target_speciality_id": f"{incoming.name}:tag:{tag_id}",
                        "target_speciality_name": tag_name,
                    })

        basis_counts = Counter(placement.get("placement_basis", NO_SOURCE_CATEGORY) for placement in placements)
        action_counts = Counter(placement["action"] for placement in placements)
        summary = (
            f"{incoming.name}: {len(new_tag_ids)} new tags against {baseline.name} - "
            f"{len(content_matched)} placed by content, {len(fallback_tag_ids)} via {len(referenced_categories)} "
            f"source categories; placements "
            + ", ".join(f"{basis}={count}" for basis, count in sorted(basis_counts.items()))
            + "; actions "
            + ", ".join(f"{action}={count}" for action, count in sorted(action_counts.items()))
        )
        return MetricResult(
            metric_id=self.id,
            status="ok",
            summary=summary,
            details={
                "baseline": baseline.name,
                "incoming": incoming.name,
                "content_comparison": content_comparison,
                "category_comparison": category_comparison,
                "placements": placements,
            },
        )

    @staticmethod
    def _content_placement(
        tag_id: Any,
        tag_name: Any,
        outcome: dict[str, Any],
        specialities_by_id: dict[Any, dict[str, Any]],
        categories_by_id: dict[Any, dict[str, Any]],
        services_by_id: dict[Any, Any],
    ) -> dict[str, Any]:
        """A tag matched to an existing baseline speciality sits wherever that speciality really is."""
        speciality = specialities_by_id[outcome["target_id"]]
        category = categories_by_id[speciality.get("category_id")]
        return {
            "tag_id": tag_id,
            "tag_name": tag_name,
            "placement_basis": CONTENT_BASIS,
            "action": outcome["action"],
            "target_category_id": category.get("id"),
            "target_category_name": category.get("name"),
            "target_service_id": category.get("service_id"),
            "target_service_name": services_by_id.get(category.get("service_id")),
            "speciality_action": outcome["action"],
            "speciality_match_type": outcome["match_type"],
            "speciality_score": outcome["score"],
            "speciality_semantic_score": outcome["semantic_score"],
            "target_speciality_id": outcome["target_id"],
            "target_speciality_name": outcome["target_name"],
            "needs_review": False,
        }
