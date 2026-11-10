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

Five opt-in params (#132) cut the one-tag and repeated specialities step 2
creates; all are off by default, so the output is unchanged unless a round
asks for them:

- `category_scoped_semantic_threshold`: before proposing the tag as a new
  speciality under a *matched* category, compare it with that category's
  existing specialities only, at this (lower) cosine threshold. A hit places
  the tag there (`placement_basis: category_content`), deepening an existing
  speciality instead of adding a one-tag one. Step 1's global threshold stays
  strict because it searches every speciality; inside the right category a
  looser match is far less likely to be wrong.
- `one_category_per_tag`: a tag linked to several source categories (one
  SkillsFuture skill in many sectors) is placed via the one with the most
  evidence only - matched categories first, then the most linking source
  specialities - instead of one placement per category. The others are named
  in the reason as evidence. This stops the same tag-named speciality being
  proposed under several categories.
- `group_new_specialities_threshold`: after placement, the tags proposed as
  new specialities under one category (resolved, or a tag's first review
  candidate) are grouped by meaning. Each group becomes one new speciality, named after
  its most central tag: the tag with the most others at or above this cosine
  threshold. Every member is within the threshold of that tag, so names can't
  drift along a chain of near neighbours.
- `group_name_model` (with `group_name_cache`): name each group from all its
  tags with this chat model instead of after its central tag, and hold the
  generated name for review. The rule and its fallbacks are in
  `speciality_names`.
- `flag_single_tag_specialities`: a new speciality still holding only its own
  tag after all of the above is flagged `needs_review`, so it can't be
  integrated unseen. It adds a review item; it doesn't change depth.

It keeps the `Metric` interface (params, dataset checks, a `MetricResult`) but is
not in the metric registry: it places tags rather than scoring a taxonomy, so
`case_a.run_case_a` calls it directly and `GET /metrics` doesn't list it.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
from rapidfuzz import fuzz, process

from ..matching.engine import KnownPool, SourceParents, compare_level, validated_float
from ..matching.names import is_valid_name, match_key, normalize
from ..matching.semantic import SemanticMatcher, SimilarityMatcher
from ..models import MetricResult
from ..quality.base import Metric
from ..taxonomy.base import Hierarchy, LoadedDataset, TaxonomyDataset
from .speciality_names import GroupNamer, GroupNamingError, LLMGroupNamer, SpecialityGroup

NEW_CATEGORY = "NEW_CATEGORY"
NEW_SPECIALITY = "NEW_SPECIALITY"
NO_SOURCE_CATEGORY = "NO_SOURCE_CATEGORY"
INVALID_CATEGORY_NAME = "INVALID_CATEGORY_NAME"
MATCHED_ACTIONS = {"SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE"}
RESOLVED_CATEGORY_ACTIONS = MATCHED_ACTIONS | {NEW_CATEGORY}
REVIEW_CATEGORY_ACTIONS = {"REVIEW_AMBIGUOUS", "REVIEW_NEW_PLACEMENT", "REVIEW_OTHER_LEVEL"}
# How a placement was reached - see the module docstring.
CONTENT_BASIS = "content"
CATEGORY_CONTENT_BASIS = "category_content"
SOURCE_CATEGORY_BASIS = "source_category"
REVIEW_CANDIDATE_BASIS = "review_candidate"
# speciality_action of a category_content placement: an existing speciality
# matched by meaning within the tag's matched category.
CATEGORY_SCOPED_MATCH = "SKIP_CATEGORY_SCOPED_MATCH"
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
    hierarchy = dataset.hierarchy()
    grouped: dict[Any, dict[Any, dict[str, Any]]] = {tag_id: {} for tag_id in tag_ids}
    for tag_id, by_category in grouped.items():
        for speciality_id in hierarchy.tag_specialities.get(tag_id, []):
            speciality = hierarchy.specialities.get(speciality_id)
            category = hierarchy.category_of(speciality)
            if category is None:
                continue
            entry = by_category.setdefault(category.get("id"), {"category": category, "specialities": {}})
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
    parents: SourceParents,
    settings: dict[str, Any],
    semantic_matcher: SemanticMatcher | None,
) -> dict[str, Any]:
    tables = {"services": baseline.services, "categories": baseline.categories, "specialities": baseline.specialities}
    tables[level] = baseline_rows
    pool = KnownPool()
    pool.add(baseline.name, TaxonomyDataset(name=baseline.name, **tables))
    return compare_level(level, source_rows, pool, parents, settings, semantic_matcher, seen_in_source={})


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
    named = [row for row in categories if is_valid_name(row.get("name"))]
    if not named or not is_valid_name(name):
        return []
    if semantic_matcher:
        scores = semantic_matcher.similarity([normalize(str(name))], [normalize(str(row["name"])) for row in named])[0]
        best = np.argsort(-np.asarray(scores), kind="stable")[:REVIEW_CANDIDATES]
        return [(named[index], round(float(scores[index]), 4), "semantic") for index in best]
    best = process.extract(
        match_key(name), [match_key(row["name"]) for row in named], scorer=fuzz.ratio, limit=REVIEW_CANDIDATES,
    )
    return [(named[index], round(float(score), 1), "lexical") for _, score, index in best]


def _evidence_rank(entry: dict[str, Any], category_outcomes: dict[Any, dict[str, Any]]) -> tuple[int, int, str]:
    """Sort key for `one_category_per_tag`: a matched category first (its ID and
    service are real), then a new one, then one only offered for review; within
    that, the source category with the most source specialities linking the tag
    to it; ties by name, so the choice never depends on input order."""
    action = category_outcomes[entry["category"].get("id")]["action"]
    tier = 0 if action in MATCHED_ACTIONS else 1 if action == NEW_CATEGORY else 2
    return tier, -len(entry["speciality_names"]), str(entry["category"].get("name"))


def _category_scoped_matches(
    tag_names: dict[Any, Any],
    pairs: set[tuple[Any, Any]],
    specialities_by_category: dict[Any, list[dict[str, Any]]],
    semantic_matcher: SimilarityMatcher,
    threshold: float,
) -> dict[tuple[Any, Any], tuple[dict[str, Any], float]]:
    """For each (tag id, baseline category id) in `pairs`, the category's existing
    speciality closest in meaning to the tag, kept only at or above `threshold`.
    One similarity call per category, so cost grows with categories, not tags."""
    tags_by_category: dict[Any, list[Any]] = {}
    for tag_id, category_id in sorted(pairs, key=lambda pair: (str(pair[1]), str(pair[0]))):
        if is_valid_name(tag_names.get(tag_id)):
            tags_by_category.setdefault(category_id, []).append(tag_id)
    matches: dict[tuple[Any, Any], tuple[dict[str, Any], float]] = {}
    for category_id, tag_ids in tags_by_category.items():
        candidates = [row for row in specialities_by_category.get(category_id, []) if is_valid_name(row.get("name"))]
        if not candidates:
            continue
        scores = np.asarray(semantic_matcher.similarity(
            [normalize(str(tag_names[tag_id])) for tag_id in tag_ids],
            [normalize(str(row["name"])) for row in candidates],
        ))
        for row_index, tag_id in enumerate(tag_ids):
            best = int(np.argmax(scores[row_index]))
            score = float(scores[row_index][best])
            if score >= threshold:
                matches[(tag_id, category_id)] = (candidates[best], round(score, 4))
    return matches


def _leader_groups(names: list[str], similarity: np.ndarray, threshold: float) -> list[tuple[int, list[int]]]:
    """Group `names` around leaders: repeatedly take the ungrouped name with the
    most ungrouped neighbours at or above `threshold` (ties by name), and group
    it with those neighbours. Returns (leader index, member indices) per group
    of two or more; every member is within `threshold` of its leader."""
    adjacent = similarity >= threshold
    np.fill_diagonal(adjacent, True)
    open_ = np.ones(len(names), dtype=bool)
    order = sorted(range(len(names)), key=lambda index: (names[index], index))
    groups = []
    while open_.any():
        counts = (adjacent & open_).sum(axis=1) * open_
        if counts.max() < 2:  # only singletons are left
            break
        leader = min(order, key=lambda index: (-counts[index], names[index], index))
        members = [index for index in order if open_[index] and adjacent[leader, index] and index != leader]
        groups.append((leader, members))
        open_[[leader, *members]] = False
    return groups


def _group_key(placement: dict[str, Any]) -> Any:
    """The category a proposed new speciality would sit under, or None if the placement proposes none.

    Of a tag's review candidates only the first counts: grouping a tag in every
    candidate category would let one leader tag name a speciality in several
    categories, recreating the repeated names `one_category_per_tag` removes.
    """
    if placement.get("speciality_action") != NEW_SPECIALITY:
        return None
    if placement.get("placement_basis") == REVIEW_CANDIDATE_BASIS:
        return placement["candidate_category_id"] if placement.get("candidate_rank") == 1 else None
    if placement.get("placement_basis") == SOURCE_CATEGORY_BASIS:
        return placement["target_category_id"]
    return None


def _group_new_specialities(
    placements: list[dict[str, Any]], semantic_matcher: SimilarityMatcher, threshold: float, incoming_name: str,
    *, namer: GroupNamer | None = None, existing_names: frozenset[str] = frozenset(),
) -> int:
    """Point the tags of each group at one new speciality, and name it.

    Grouping is per category (`_group_key`), on distinct tags, so a tag reaching
    one category twice is grouped once. The name is the leader tag's unless
    `namer` gives a usable one that repeats neither `existing_names` (normalized
    baseline speciality names) nor another new speciality (see
    `speciality_names`). Returns how many groups were formed.
    """
    tags_by_key: dict[Any, dict[Any, str]] = {}
    category_names: dict[Any, str] = {}
    for placement in placements:
        key = _group_key(placement)
        if key is not None and is_valid_name(placement.get("tag_name")):
            tags_by_key.setdefault(key, {})[placement["tag_id"]] = str(placement["tag_name"])
            category_names[key] = str(placement.get("candidate_category_name") or placement["target_category_name"])
    groups: list[tuple[Any, Any, list[tuple[Any, float]]]] = []  # (key, leader tag id, [(member id, score)])
    for key, tags in sorted(tags_by_key.items(), key=lambda item: str(item[0])):
        if len(tags) < 2:
            continue
        tag_ids = sorted(tags, key=lambda tag_id: (tags[tag_id], str(tag_id)))
        names = [normalize(tags[tag_id]) for tag_id in tag_ids]
        similarity = np.asarray(semantic_matcher.similarity(names, names))
        for leader, members in _leader_groups(names, similarity, threshold):
            groups.append((key, tag_ids[leader], [
                (tag_ids[index], round(float(similarity[index, leader]), 4)) for index in members
            ]))

    generated: list[str | None] = [None] * len(groups)
    if namer is not None and groups:
        generated = namer.name([
            SpecialityGroup(category_names[key], (tags_by_key[key][leader], *(tags_by_key[key][m] for m, _ in members)))
            for key, leader, members in groups
        ])
    grouped = {(key, tag_id) for key, leader, members in groups for tag_id in (leader, *(m for m, _ in members))}
    taken = set(existing_names) | {
        normalize(str(placement["target_speciality_name"])) for placement in placements
        if _group_key(placement) is not None and (_group_key(placement), placement["tag_id"]) not in grouped
    }
    assignment: dict[tuple[Any, Any], dict[str, Any]] = {}
    for (key, leader_id, members), name in zip(groups, generated, strict=True):
        tags = tags_by_key[key]
        group: dict[str, Any] = {
            "target_speciality_id": f"{incoming_name}:tag:{leader_id}",
            "target_speciality_name": tags[leader_id],
            "speciality_group_size": len(members) + 1,
            "speciality_group_central_tag": tags[leader_id],
            "speciality_group_sample": [tags[member_id] for member_id, _ in members[:EVIDENCE_SAMPLE]],
            "speciality_name_source": "central_tag",
        }
        if namer is not None:
            if name is not None and normalize(name) not in taken:
                group.update(target_speciality_name=name, speciality_name_source="generated",
                             speciality_name_model=namer.model, needs_review=True)
            else:
                group["speciality_name_fallback"] = (
                    "no usable name came back" if name is None else f"the generated name {name!r} already exists"
                )
        taken.add(normalize(str(group["target_speciality_name"])))
        assignment[(key, leader_id)] = {**group, "speciality_group_leader": True}
        for member_id, score in members:
            assignment[(key, member_id)] = {**group, "speciality_group_leader": False, "speciality_group_score": score}
    for placement in placements:
        key = _group_key(placement)
        if key is not None and (key, placement["tag_id"]) in assignment:
            placement.update(assignment[(key, placement["tag_id"])])
    return len(groups)


def _flag_single_tag_specialities(placements: list[dict[str, Any]]) -> int:
    """Flag `needs_review` on every new speciality that holds only one tag; returns how many."""
    tags: dict[tuple[Any, Any], set[Any]] = {}
    for placement in placements:
        key = _group_key(placement)
        if key is not None:
            tags.setdefault((key, placement["target_speciality_id"]), set()).add(placement["tag_id"])
    flagged = 0
    for placement in placements:
        key = _group_key(placement)
        if key is not None and len(tags[(key, placement["target_speciality_id"])]) == 1:
            placement["needs_review"] = placement["single_tag_speciality"] = True
            flagged += 1
    return flagged


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
        # #132, both off by default - see the module docstring.
        "category_scoped_semantic_threshold": None,
        "one_category_per_tag": False,
        "group_new_specialities_threshold": None,
        "flag_single_tag_specialities": False,
        "group_name_model": None,
        "group_name_cache": None,
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
                    "REVIEW_AMBIGUOUS (see enrichment/routing.py)."
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
            threshold = validated_float(p, "threshold", 0, 100)
            semantic_threshold = validated_float(p, "semantic_threshold", -1, 1)
            add_candidate_lexical_threshold = validated_float(p, "add_candidate_lexical_threshold", 0, 100)
            add_candidate_semantic_threshold = validated_float(p, "add_candidate_semantic_threshold", -1, 1)
            scoped_threshold = (
                validated_float(p, "category_scoped_semantic_threshold", -1, 1)
                if p["category_scoped_semantic_threshold"] is not None else None
            )
            group_threshold = (
                validated_float(p, "group_new_specialities_threshold", -1, 1)
                if p["group_new_specialities_threshold"] is not None else None
            )
        except (TypeError, ValueError) as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        one_category_per_tag = p["one_category_per_tag"]
        if not isinstance(one_category_per_tag, bool):
            return MetricResult(metric_id=self.id, status="error", summary="one_category_per_tag must be true or false")
        flag_single_tag = p["flag_single_tag_specialities"]
        if not isinstance(flag_single_tag, bool):
            return MetricResult(
                metric_id=self.id, status="error", summary="flag_single_tag_specialities must be true or false",
            )
        try:
            semantic_matcher = SemanticMatcher(str(p["semantic_model"])) if p["semantic_model"] else None
        except ValueError as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        if scoped_threshold is not None and semantic_matcher is None:
            return MetricResult(
                metric_id=self.id, status="error",
                summary="category_scoped_semantic_threshold needs a semantic_model: it matches by meaning only",
            )
        if group_threshold is not None and semantic_matcher is None:
            return MetricResult(
                metric_id=self.id, status="error",
                summary="group_new_specialities_threshold needs a semantic_model: it groups by meaning only",
            )
        namer: GroupNamer | None = None
        if p["group_name_model"]:
            if group_threshold is None:
                return MetricResult(
                    metric_id=self.id, status="error",
                    summary="group_name_model names grouped specialities; set group_new_specialities_threshold too",
                )
            try:
                namer = LLMGroupNamer(str(p["group_name_model"]), cache_path=p["group_name_cache"])
            except GroupNamingError as exc:
                return MetricResult(metric_id=self.id, status="error", summary=str(exc))

        settings = {
            "threshold": threshold,
            "semantic_threshold": semantic_threshold,
            "add_candidate_lexical_threshold": add_candidate_lexical_threshold,
            "add_candidate_semantic_threshold": add_candidate_semantic_threshold,
            "cross_level": False,
            "semantic_threshold_by_level": {},
        }
        parents = SourceParents(incoming)
        known = baseline.hierarchy()
        tag_rows = [row for row in incoming.tags if row.get("id") in new_tag_ids]

        # Step 1, content: the tag against every baseline speciality. A tag row
        # has no category_id, so the parent check is skipped (no_incoming_parent)
        # and a name match is a plain match, never REVIEW_NEW_PLACEMENT.
        content_comparison = _match_against_pool(
            "specialities", tag_rows, baseline.specialities, baseline, parents, settings, semantic_matcher,
        )
        content_outcomes = _outcomes_by_incoming_id(content_comparison, NEW_SPECIALITY)
        # A content match only counts when the matched speciality's category and
        # service both exist: the contract needs all three, and a record with a
        # category but no service would fail validation for the whole batch.
        content_matched = {}
        for tag_id, outcome in content_outcomes.items():
            matched_category = known.category_of(known.specialities.get(outcome["target_id"]))
            if (
                outcome["action"] in MATCHED_ACTIONS and matched_category is not None
                and known.service_name(matched_category.get("service_id")) is not None
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
            row for row in baseline.categories if known.service_name(row.get("service_id")) is not None
        ]
        review_candidates = {
            category_id: _closest_categories(row.get("name"), reviewable_categories, semantic_matcher)
            for category_id, row in referenced_categories.items()
            if category_outcomes[category_id]["action"] in REVIEW_CATEGORY_ACTIONS
        }
        matched_baseline_rows = [
            known.categories[outcome["target_id"]]
            for outcome in category_outcomes.values()
            if outcome["action"] in MATCHED_ACTIONS and known.categories.get(outcome["target_id"]) is not None
        ]
        if one_category_per_tag:
            for tag_id, entries in source_categories_by_tag.items():
                if len(entries) > 1:
                    best, *others = sorted(entries, key=lambda entry: _evidence_rank(entry, category_outcomes))
                    source_categories_by_tag[tag_id] = [{
                        **best, "other_source_categories": [str(entry["category"].get("name")) for entry in others],
                    }]
        scoped_matches: dict[tuple[Any, Any], tuple[dict[str, Any], float]] = {}
        if scoped_threshold is not None and semantic_matcher is not None:
            pairs = {
                (tag_id, category_outcomes[entry["category"].get("id")]["target_id"])
                for tag_id, entries in source_categories_by_tag.items() for entry in entries
                if category_outcomes[entry["category"].get("id")]["action"] in MATCHED_ACTIONS
            }
            specialities_by_category: dict[Any, list[dict[str, Any]]] = {}
            for speciality in baseline.specialities:
                specialities_by_category.setdefault(speciality.get("category_id"), []).append(speciality)
            scoped_matches = _category_scoped_matches(
                {row.get("id"): row.get("name") for row in tag_rows}, pairs, specialities_by_category,
                semantic_matcher, scoped_threshold,
            )

        placements: list[dict[str, Any]] = []
        for row in tag_rows:
            tag_id, tag_name = row.get("id"), row.get("name")
            if tag_id in content_matched:
                placements.append(self._content_placement(tag_id, tag_name, content_matched[tag_id], known))
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
                if "other_source_categories" in entry:
                    placement["other_source_categories"] = entry["other_source_categories"]
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
                    placement["target_service_id"] = known.categories[category_outcome["target_id"]].get(
                        "service_id"
                    )
                elif category_outcome["action"] == NEW_CATEGORY:
                    placement["target_service_id"] = _assign_service(matched_baseline_rows)
                if category_outcome["action"] in RESOLVED_CATEGORY_ACTIONS:
                    placement["target_service_name"] = known.service_name(placement["target_service_id"])
                    # Step 1 already searched every baseline speciality at the
                    # global threshold, so the tag itself is the proposed
                    # speciality - unless the opt-in category-scoped search
                    # finds a close enough one inside this matched category.
                    placement["speciality_action"] = NEW_SPECIALITY
                    placement["target_speciality_id"] = f"{incoming.name}:tag:{tag_id}"
                    placement["target_speciality_name"] = tag_name
                    scoped = scoped_matches.get((tag_id, category_outcome["target_id"]))
                    if category_outcome["action"] in MATCHED_ACTIONS and scoped is not None:
                        speciality, score = scoped
                        placement.update({
                            "placement_basis": CATEGORY_CONTENT_BASIS,
                            "speciality_action": CATEGORY_SCOPED_MATCH,
                            "target_speciality_id": speciality.get("id"),
                            "target_speciality_name": speciality.get("name"),
                            "speciality_semantic_score": score,
                        })
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
                        "candidate_service_name": known.service_name(candidate.get("service_id")),
                        "speciality_action": NEW_SPECIALITY,
                        "target_speciality_id": f"{incoming.name}:tag:{tag_id}",
                        "target_speciality_name": tag_name,
                    })

        try:
            groups = (
                _group_new_specialities(
                    placements, semantic_matcher, group_threshold, incoming.name, namer=namer,
                    existing_names=frozenset(normalize(str(row.get("name"))) for row in baseline.specialities),
                )
                if group_threshold is not None and semantic_matcher is not None else 0
            )
        except GroupNamingError as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        flagged = _flag_single_tag_specialities(placements) if flag_single_tag else 0
        named = sum(
            1 for placement in placements
            if placement.get("speciality_group_leader") and placement.get("speciality_name_source") == "generated"
        )

        basis_counts = Counter(placement.get("placement_basis", NO_SOURCE_CATEGORY) for placement in placements)
        action_counts = Counter(placement["action"] for placement in placements)
        summary = (
            f"{incoming.name}: {len(new_tag_ids)} new tags against {baseline.name} - "
            f"{len(content_matched)} placed by content, {len(fallback_tag_ids)} via {len(referenced_categories)} "
            f"source categories; placements "
            + ", ".join(f"{basis}={count}" for basis, count in sorted(basis_counts.items()))
            + "; actions "
            + ", ".join(f"{action}={count}" for action, count in sorted(action_counts.items()))
            + (f"; {groups} new-speciality groups" if group_threshold is not None else "")
            + (f" ({named} named by the model)" if namer is not None else "")
            + (f"; {flagged} single-tag placements flagged" if flag_single_tag else "")
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
        known: Hierarchy,
    ) -> dict[str, Any]:
        """A tag matched to an existing baseline speciality sits wherever that speciality really is."""
        speciality = known.specialities[outcome["target_id"]]
        category = known.categories[speciality.get("category_id")]
        return {
            "tag_id": tag_id,
            "tag_name": tag_name,
            "placement_basis": CONTENT_BASIS,
            "action": outcome["action"],
            "target_category_id": category.get("id"),
            "target_category_name": category.get("name"),
            "target_service_id": category.get("service_id"),
            "target_service_name": known.service_name(category.get("service_id")),
            "speciality_action": outcome["action"],
            "speciality_match_type": outcome["match_type"],
            "speciality_score": outcome["score"],
            "speciality_semantic_score": outcome["semantic_score"],
            "target_speciality_id": outcome["target_id"],
            "target_speciality_name": outcome["target_name"],
            "needs_review": False,
        }
