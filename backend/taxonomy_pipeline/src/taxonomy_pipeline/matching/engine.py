"""The comparison engine saturation and Case A share: is each incoming concept already known?

`KnownPool` holds every concept seen so far by level; `SourceParents` resolves a row's parents
inside its own source; `compare_level` gives each unique incoming concept at one level one
verdict (a duplicate to skip, a known concept in a new placement or at another level, an add
candidate, or one to review), optionally asking the LLM judge about the ambiguous ones. The
evidence tiers and verdicts are described in `quality/saturation.py`, which turns these
per-level results into the saturation curve. Case A (`enrichment/category_adaptation.py`)
runs the same comparison against a reduced pool to place tags.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import numpy as np

from ..taxonomy.base import TaxonomyDataset
from ..taxonomy.synonym_set import SynonymMapping
from .judge import NONE_CHOICE, JudgeCandidate, JudgeQuestion
from .names import is_valid_name, lexical_scores, match_key, normalize
from .semantic import SemanticMatcher

CONCEPT_LEVELS = ("categories", "specialities", "tags")
SKIP_DUPLICATE = "SKIP_DUPLICATE"
SKIP_SEMANTIC_DUPLICATE = "SKIP_SEMANTIC_DUPLICATE"
REVIEW_NEW_PLACEMENT = "REVIEW_NEW_PLACEMENT"
REVIEW_OTHER_LEVEL = "REVIEW_OTHER_LEVEL"
ADD_CANDIDATE = "ADD_CANDIDATE"
REVIEW_AMBIGUOUS = "REVIEW_AMBIGUOUS"
HAS_PARENT = "HAS_PARENT"
NO_PARENT = "NO_PARENT"
PARENT_LEVEL = {"categories": "services", "specialities": "categories", "tags": "specialities"}
# Parents shown to the judge per concept: enough for context, few enough to keep requests small.
JUDGE_CONTEXT = 3


def group_concepts(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Group valid named rows by match key (first-seen order), so a concept
    repeated under several parents counts once. Returns (groups, invalid)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    invalid = []
    for row in rows:
        name = row.get("name")
        if not is_valid_name(name):
            invalid.append({"incoming_id": row.get("id"), "incoming_name": name})
            continue
        groups.setdefault(match_key(name), []).append(row)
    return groups, invalid


class SourceParents:
    """Resolves each row's parents inside its own source dataset only."""

    def __init__(self, dataset: TaxonomyDataset):
        self.hierarchy = dataset.hierarchy()

    def lookup(self, level: str, row: dict[str, Any]) -> list[dict[str, Any] | None]:
        return self.hierarchy.parents(level, row)

    def classify(self, level: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        parents: dict[Any, dict[str, Any]] = {}
        for row in rows:
            for parent in self.lookup(level, row):
                # Same filter as KnownPool.add, so a placeholder parent on
                # both sides can't look like a new placement.
                if parent is None or not is_valid_name(parent.get("name")):
                    continue
                if parent.get("id") not in parents:
                    parents[parent.get("id")] = {"id": parent.get("id"), "name": parent.get("name")}
        return {
            "parent_status": HAS_PARENT if parents else NO_PARENT,
            "parent_level": PARENT_LEVEL[level],
            "parent_count": len(parents),
            "parents": list(parents.values()),
        }


class KnownPool:
    """Every concept seen so far (baseline + earlier incoming sources), by level."""

    def __init__(self):
        self.by_level: dict[str, dict[str, dict[str, Any]]] = {level: {} for level in CONCEPT_LEVELS}

    def add(self, source: str, dataset: TaxonomyDataset) -> None:
        """Add a source's concepts. Parents of *every* row sharing a key are
        merged into the known concept - also for keys already known - so a
        placement seen in any earlier source is never flagged again."""
        parents = SourceParents(dataset)
        for level in CONCEPT_LEVELS:
            known = self.by_level[level]
            for key, rows in group_concepts(getattr(dataset, level))[0].items():
                if key not in known:
                    row = rows[0]
                    known[key] = {
                        "source": source, "id": row.get("id"), "name": row.get("name"),
                        "normalized": normalize(str(row.get("name"))), "status": row.get("status"),
                        "parents": {},
                    }
                for row in rows:
                    for parent in parents.lookup(level, row):
                        if parent is not None and is_valid_name(parent.get("name")):
                            known[key]["parents"].setdefault(match_key(parent["name"]), {
                                "name": parent["name"], "normalized": normalize(str(parent["name"])),
                            })

    def add_aliases(self, synonyms: Iterable[SynonymMapping]) -> tuple[int, list[SynonymMapping]]:
        """Let each global synonym's term match the concept it names, as an exact match on that concept.

        Only global synonyms apply: a scoped one needs the branch a term is used in, and an
        incoming concept's branch belongs to another taxonomy. A synonym applies only where
        the known taxonomy has its concept with the same ID and name; any other is dropped
        and returned, so a set exported from another dataset can't attach terms to a
        same-named but different concept. A term that is already a known name keeps its own
        meaning. Returns how many terms were added, and the dropped synonyms.
        """
        added, dropped = 0, []
        for synonym in synonyms:
            if synonym.scope:
                continue
            known = self.by_level.get(synonym.concept_level)
            concept = known.get(match_key(synonym.concept_name)) if known is not None else None
            if concept is None or str(concept.get("id")) != synonym.concept_id:
                dropped.append(synonym)
                continue
            alias = match_key(synonym.term)
            if not alias or alias in known:
                continue
            known[alias] = concept
            added += 1
        return added, dropped

    def size(self) -> int:
        """Known concepts, not keys: a synonym alias shares its concept's entry."""
        return sum(len({id(entry) for entry in known.values()}) for known in self.by_level.values())

    def candidates(self, level: str, *, other_levels: bool) -> dict[str, tuple[str, dict[str, Any]]]:
        """Known concepts to compare a `level` concept against, as key -> (level, entry):
        that level alone, or the other levels combined (a key found at two other
        levels resolves to the first in CONCEPT_LEVELS order)."""
        levels = [other for other in CONCEPT_LEVELS if other != level] if other_levels else [level]
        found: dict[str, tuple[str, dict[str, Any]]] = {}
        for candidate_level in levels:
            for key, entry in self.by_level[candidate_level].items():
                found.setdefault(key, (candidate_level, entry))
        return found


def _match_entry(
    row: dict[str, Any], level: str, known: dict[str, Any], action: str, **extra: Any
) -> dict[str, Any]:
    return {
        "incoming_id": row.get("id"),
        "incoming_name": str(row["name"]),
        "baseline_id": known["id"],
        "baseline_name": known["name"],
        "baseline_level": level,
        "matched_source": known["source"],
        "baseline_status": known["status"],
        **extra,
        "recommended_action": action,
    }


def _is_inactive(entry: dict[str, Any]) -> bool:
    return str(entry.get("baseline_status")) == "0"


def _parent_status_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    return {status: sum(entry["parent_status"] == status for entry in entries) for status in (HAS_PARENT, NO_PARENT)}


def rate(count: int, total: int) -> float | None:
    return round(count / total, 3) if total else None


class _Evidence:
    """Best exact / lexical / semantic match of one concept within one candidate set.
    Each match is (level, known entry, score), or None."""

    __slots__ = ("exact", "lexical", "semantic")

    def __init__(self):
        self.exact: tuple[str, dict[str, Any], float] | None = None
        self.lexical: tuple[str, dict[str, Any], float] | None = None
        self.semantic: tuple[str, dict[str, Any], float] | None = None

    def close_spelling(self, p: dict[str, Any]) -> bool:
        return self.lexical is not None and self.lexical[2] >= p["threshold"]

    def semantic_agrees(self, p: dict[str, Any]) -> bool:
        return self.semantic is not None and self.semantic[2] >= p["semantic_threshold"]


def _gather_evidence(
    concepts: list[tuple[str, list[dict[str, Any]]]],
    candidates: dict[str, tuple[str, dict[str, Any]]],
    semantic_matcher: SemanticMatcher | None,
) -> list[_Evidence]:
    evidence = [_Evidence() for _ in concepts]
    if not concepts or not candidates:
        return evidence
    keys = list(candidates)
    for item, (key, _) in zip(evidence, concepts, strict=True):
        if key in candidates:
            item.exact = (*candidates[key], 100.0)

    scores = lexical_scores([key for key, _ in concepts], keys)
    for item, row_scores in zip(evidence, scores, strict=True):
        index = int(np.argmax(row_scores))
        item.lexical = (*candidates[keys[index]], float(row_scores[index]))

    if semantic_matcher:
        key_by_normalized: dict[str, str] = {}
        for key in keys:
            key_by_normalized.setdefault(candidates[key][1]["normalized"], key)
        results = semantic_matcher.best_matches(
            [normalize(str(rows[0]["name"])) for _, rows in concepts], list(key_by_normalized)
        )
        for item, (name, score) in zip(evidence, results, strict=True):
            if name in key_by_normalized and score is not None:
                item.semantic = (*candidates[key_by_normalized[name]], score)
    return evidence


def _best(*matches: tuple[str, dict[str, Any], float] | None) -> tuple[str, dict[str, Any], float] | None:
    """Highest-scoring match; earlier arguments (the same level) win ties."""
    best = None
    for match in matches:
        if match is not None and (best is None or match[2] > best[2]):
            best = match
    return best


def _uncovered_parents(
    incoming_parents: list[dict[str, Any]], known: dict[str, Any], p: dict[str, Any],
    semantic_matcher: SemanticMatcher | None,
) -> list[dict[str, Any]]:
    """Incoming parents with no similar parent on the known concept. Similar
    means the same match key or, with a model, semantic >= semantic_threshold
    (which a close spelling also needs, so it adds nothing on its own)."""
    known_parents = known["parents"]
    uncovered = [parent for parent in incoming_parents if match_key(parent["name"]) not in known_parents]
    if uncovered and known_parents and semantic_matcher:
        scores = semantic_matcher.similarity(
            [normalize(str(parent["name"])) for parent in uncovered],
            [entry["normalized"] for entry in known_parents.values()],
        )
        uncovered = [
            parent for parent, row_scores in zip(uncovered, scores, strict=True)
            if float(np.max(row_scores)) < p["semantic_threshold"]
        ]
    return uncovered


def _ambiguous_entry(
    row: dict[str, Any], incoming_ids: list[Any], same: _Evidence, other: _Evidence,
    parent_info: dict[str, Any], p: dict[str, Any],
) -> dict[str, Any]:
    """No confident match at any level: ADD_CANDIDATE only when nothing at any
    level is even moderately close, otherwise REVIEW_AMBIGUOUS."""
    lexical_best = _best(same.lexical, other.lexical)
    semantic_best = _best(same.semantic, other.semantic)
    if same.close_spelling(p) or other.close_spelling(p):
        action, reason = REVIEW_AMBIGUOUS, "close_spelling_unconfirmed"
    else:
        low_lexical = lexical_best is None or lexical_best[2] < p["add_candidate_lexical_threshold"]
        low_semantic = semantic_best is None or semantic_best[2] < p["add_candidate_semantic_threshold"]
        if low_lexical and low_semantic:
            action, reason = ADD_CANDIDATE, "no_close_match"
        else:
            action, reason = REVIEW_AMBIGUOUS, "partial_similarity"
    return {
        "incoming_id": row.get("id"),
        "incoming_ids": incoming_ids,
        "incoming_name": str(row["name"]),
        "closest_baseline_name": lexical_best[1]["name"] if lexical_best else None,
        "closest_baseline_score": round(lexical_best[2], 1) if lexical_best else None,
        "closest_baseline_level": lexical_best[0] if lexical_best else None,
        "closest_semantic_baseline_name": semantic_best[1]["name"] if semantic_best else None,
        "closest_semantic_score": semantic_best[2] if semantic_best else None,
        "closest_semantic_baseline_level": semantic_best[0] if semantic_best else None,
        "reason": reason,
        **parent_info,
        "recommended_action": action,
    }


def _winning_match(
    same: _Evidence, other: _Evidence, p: dict[str, Any]
) -> tuple[str, tuple[str, dict[str, Any], float], _Evidence] | None:
    """Strongest evidence of a similar concept, as (match_type, match, evidence
    set it came from); within a tier the same level wins."""
    for evidence in (same, other):
        if evidence.exact:
            return "exact", evidence.exact, evidence
    for evidence in (same, other):
        if evidence.close_spelling(p) and evidence.semantic_agrees(p):
            return "close_spelling", evidence.lexical, evidence
    for evidence in (same, other):
        if evidence.semantic_agrees(p):
            return "semantic", evidence.semantic, evidence
    return None


def _judge_candidates(
    level: str, pool: KnownPool, cross_level: bool,
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Known concepts the judge may pick from, by normalized name: this level first, then the others."""
    found: dict[str, tuple[str, dict[str, Any]]] = {}
    sets = [pool.candidates(level, other_levels=False)]
    if cross_level:
        sets.append(pool.candidates(level, other_levels=True))
    for candidates in sets:
        for known_level, entry in candidates.values():
            found.setdefault(entry["normalized"], (known_level, entry))
    return found


def _ask_judge(
    level: str,
    items: list[tuple[list[dict[str, Any]], _Evidence, dict[str, Any]]],
    pool: KnownPool,
    parents: SourceParents,
    p: dict[str, Any],
    semantic_matcher: SemanticMatcher,
) -> tuple[list[tuple[list[dict[str, Any]], str, tuple[str, dict[str, Any], float], _Evidence]], dict[str, int]]:
    """Ask the judge about each REVIEW_AMBIGUOUS concept in `items` (rows, evidence, ambiguous entry).

    Entries are updated in place: judged new -> ADD_CANDIDATE, unsure or failed ->
    still REVIEW_AMBIGUOUS with the reason saying so, each with the verdict
    attached. Judged duplicates are returned as matches for the caller to move
    out of the ambiguous list, with the judge's probability as their score.
    """
    judge, cfg = p["judge"]
    counts = {"asked": 0, "judged_duplicate": 0, "judged_new": 0, "unsure": 0, "failed": 0}
    candidates = _judge_candidates(level, pool, p["cross_level"])
    review = [item for item in items if item[2]["recommended_action"] == REVIEW_AMBIGUOUS]
    if not review or not candidates:
        return [], counts
    names = list(candidates)
    tops = semantic_matcher.top_matches(
        [normalize(str(rows[0]["name"])) for rows, _, _ in review], names, cfg["candidates"]
    )
    questions = []
    for (rows, _, _), top in zip(review, tops, strict=True):
        questions.append(JudgeQuestion(
            name=str(rows[0]["name"]), level=level,
            parents=tuple(parent["name"] for parent in parents.classify(level, rows)["parents"][:JUDGE_CONTEXT]),
            candidates=tuple(
                JudgeCandidate(
                    name=str(candidates[name][1]["name"]), level=candidates[name][0],
                    parents=tuple(entry["name"] for entry in candidates[name][1]["parents"].values())[:JUDGE_CONTEXT],
                )
                for name, _ in top
            ),
        ))
    verdicts = judge.judge(questions)

    matches = []
    for (rows, evidence, entry), top, verdict in zip(review, tops, verdicts, strict=True):
        counts["asked"] += 1
        entry["judge"] = {
            "model": judge.model, "choice": verdict.choice, "probability": verdict.probability,
            "candidates": [candidates[name][1]["name"] for name, _ in top],
            **({"error": verdict.error} if verdict.error else {}),
        }
        index = verdict.candidate_index
        if verdict.error:
            counts["failed"] += 1
            entry["reason"] = "judge_failed"
        elif index is not None and index < len(top) and (verdict.probability or 0) >= cfg["same_at_or_above"]:
            counts["judged_duplicate"] += 1
            name, semantic_score = top[index]
            known_level, known_entry = candidates[name]
            evidence.semantic = (known_level, known_entry, semantic_score)
            matches.append((rows, "judged", (known_level, known_entry, float(verdict.probability)), evidence))
        elif verdict.choice == NONE_CHOICE and (verdict.probability or 0) >= cfg["new_at_or_above"]:
            counts["judged_new"] += 1
            entry["recommended_action"], entry["reason"] = ADD_CANDIDATE, "judged_new"
        else:
            counts["unsure"] += 1
            entry["reason"] = "judge_unsure"
    return matches, counts


def compare_level(
    level: str,
    incoming_rows: list[dict[str, Any]],
    pool: KnownPool,
    parents: SourceParents,
    p: dict[str, Any],
    semantic_matcher: SemanticMatcher | None,
    seen_in_source: dict[str, str],
) -> dict[str, Any]:
    groups, invalid = group_concepts(incoming_rows)
    # Concept matching may use a stricter per-level meaning threshold (e.g.
    # job titles vs skills); the parent check keeps the global one.
    level_p = {**p, "semantic_threshold": p["semantic_threshold_by_level"].get(level, p["semantic_threshold"])}
    # A name already counted at an earlier level of this same source is the
    # same concept; count it once.
    repeated = [
        {"incoming_ids": [r.get("id") for r in rows], "incoming_name": str(rows[0]["name"]),
         "first_counted_level": seen_in_source[key]}
        for key, rows in groups.items() if key in seen_in_source
    ]
    groups = {key: rows for key, rows in groups.items() if key not in seen_in_source}
    seen_in_source.update(dict.fromkeys(groups, level))

    exact_matches: list[dict[str, Any]] = []
    fuzzy_matches: list[dict[str, Any]] = []
    semantic_matches: list[dict[str, Any]] = []
    new_placements: list[dict[str, Any]] = []
    other_level_matches: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    placements_checked = 0

    matched: list[tuple[list[dict[str, Any]], str, tuple[str, dict[str, Any], float], _Evidence]] = []
    remaining = list(groups.items())

    same_evidence = _gather_evidence(remaining, pool.candidates(level, other_levels=False), semantic_matcher)
    other_candidates = pool.candidates(level, other_levels=True) if p["cross_level"] else {}
    other_evidence = _gather_evidence(remaining, other_candidates, semantic_matcher)
    unmatched: list[tuple[list[dict[str, Any]], _Evidence, dict[str, Any]]] = []
    for (_, rows), same, other in zip(remaining, same_evidence, other_evidence, strict=True):
        winner = _winning_match(same, other, level_p)
        if winner is None:
            entry = _ambiguous_entry(
                rows[0], [r.get("id") for r in rows], same, other, parents.classify(level, rows), level_p
            )
            ambiguous.append(entry)
            unmatched.append((rows, same, entry))
        else:
            matched.append((rows, *winner))

    judge_report: dict[str, Any] = {}
    # The verdict _ask_judge records on each entry, so a judged duplicate keeps it once moved out of the list.
    judge_records: dict[int, dict[str, Any]] = {}
    if p.get("judge") and level in p["judge"][1]["levels"] and semantic_matcher:
        before = sum(entry["recommended_action"] == REVIEW_AMBIGUOUS for entry in ambiguous)
        judged, judge_counts = _ask_judge(level, unmatched, pool, parents, level_p, semantic_matcher)
        judged_ids = {id(rows) for rows, *_ in judged}
        judge_records = {id(rows): entry["judge"] for rows, _, entry in unmatched if "judge" in entry}
        ambiguous = [entry for rows, _, entry in unmatched if id(rows) not in judged_ids]
        matched.extend(judged)
        judge_report = {"review_ambiguous_before_judge": before, "judge_counts": judge_counts}

    judged_matches: list[dict[str, Any]] = []
    skips_by_type = {
        "exact": exact_matches, "close_spelling": fuzzy_matches, "semantic": semantic_matches, "judged": judged_matches,
    }
    for rows, match_type, (known_level, known_entry, score), evidence in matched:
        row = rows[0]
        parent_info = parents.classify(level, rows)
        semantic_score = evidence.semantic[2] if evidence.semantic else None
        extra: dict[str, Any] = {"match_type": match_type}
        if match_type == "judged":
            extra.update(judge_probability=score, semantic_score=semantic_score, judge=judge_records[id(rows)])
        elif match_type == "semantic":
            extra.update(
                semantic_score=score,
                closest_lexical_baseline_name=evidence.lexical[1]["name"] if evidence.lexical else None,
                closest_lexical_score=round(evidence.lexical[2], 1) if evidence.lexical else None,
            )
        else:
            extra.update(score=round(score, 1), semantic_score=semantic_score)
            if match_type == "close_spelling":
                extra["semantic_baseline_name"] = evidence.semantic[1]["name"]

        if level == "tags":
            parent_check = "not_checked_for_tags"
        elif not parent_info["parents"]:
            parent_check = "no_incoming_parent"
        elif known_level != level:
            # Parents are different kinds here (e.g. a sector vs the
            # specialities a tag hangs off), so they can't be compared.
            other_level_matches.append({
                **_match_entry(row, known_level, known_entry, REVIEW_OTHER_LEVEL, **extra),
                "incoming_ids": [r.get("id") for r in rows],
                "reason": "parent_not_comparable",
                **parent_info,
            })
            continue
        else:
            placements_checked += 1
            uncovered = _uncovered_parents(parent_info["parents"], known_entry, p, semantic_matcher)
            if uncovered:
                new_placements.append({
                    **_match_entry(row, known_level, known_entry, REVIEW_NEW_PLACEMENT, **extra),
                    "incoming_ids": [r.get("id") for r in rows],
                    "unmatched_incoming_parents": uncovered,
                    "existing_parents": [entry["name"] for entry in known_entry["parents"].values()],
                    "reason": "parent_differs" if known_entry["parents"] else "existing_has_no_parent",
                    **parent_info,
                })
                continue
            parent_check = "same_parent"

        action = SKIP_SEMANTIC_DUPLICATE if match_type in {"semantic", "judged"} else SKIP_DUPLICATE
        skips_by_type[match_type].append(
            {
                **_match_entry(row, known_level, known_entry, action, **extra, parent_check=parent_check),
                "incoming_ids": [r.get("id") for r in rows],
                **parent_info,
            }
        )

    reviewed = len(groups)
    add_count = sum(item["recommended_action"] == ADD_CANDIDATE for item in ambiguous)
    review_count = len(ambiguous) - add_count
    flagged = ambiguous + new_placements + other_level_matches
    matches = (
        exact_matches + fuzzy_matches + semantic_matches + judged_matches + new_placements + other_level_matches
    )
    return {
        "relevant_concepts_reviewed": reviewed,
        "exact_matches": len(exact_matches),
        "fuzzy_candidate_matches": len(fuzzy_matches),
        "semantic_candidate_matches": len(semantic_matches),
        "other_level_matches": sum(entry["baseline_level"] != level for entry in matches),
        "ambiguous": len(ambiguous),
        "ambiguous_rate": rate(len(ambiguous), reviewed),
        "new_rate_min": rate(add_count, reviewed),
        "new_rate_max": rate(add_count + review_count, reviewed),
        "new_placements": len(new_placements),
        "placements_checked": placements_checked,
        "new_placement_rate": rate(len(new_placements), placements_checked),
        "matched_inactive": sum(_is_inactive(entry) for entry in matches),
        "skipped_invalid": invalid,
        "repeated_in_source_other_level": repeated,
        "recommended_action_counts": {
            SKIP_DUPLICATE: len(exact_matches) + len(fuzzy_matches),
            SKIP_SEMANTIC_DUPLICATE: len(semantic_matches) + len(judged_matches),
            REVIEW_NEW_PLACEMENT: len(new_placements),
            REVIEW_OTHER_LEVEL: len(other_level_matches),
            ADD_CANDIDATE: add_count,
            REVIEW_AMBIGUOUS: review_count,
        },
        "parent_status_counts": _parent_status_counts(matches + ambiguous),
        "parent_status_counts_flagged": _parent_status_counts(flagged),
        "exact_match_details": exact_matches,
        "fuzzy_match_details": fuzzy_matches,
        "semantic_match_details": semantic_matches,
        "new_placement_details": new_placements,
        "other_level_match_details": other_level_matches,
        "ambiguous_concepts": ambiguous,
        # Only when a judge ran on this level, so reports without one are unchanged.
        **({**judge_report, "judged_candidate_matches": len(judged_matches), "judged_match_details": judged_matches}
           if judge_report else {}),
    }


def validated_float(p: dict[str, Any], name: str, low: float, high: float) -> float:
    value = float(p[name])
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite and between {low:g} and {high:g}")
    return value
