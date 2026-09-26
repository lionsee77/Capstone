"""Taxonomy saturation: how many genuinely new concepts each incoming source adds.

Incoming sources are compared in order. Source k is checked against the
baseline plus every concept from sources 1..k-1, so a falling new-items rate
across steps (the `curve`) is evidence the taxonomy is approaching saturation.

Each unique incoming concept (named rows grouped by `match_key`) gets one
verdict. First, is there a similar concept at any level of what is known?
Evidence tiers run strongest first - exact key, close spelling confirmed by
the meaning model, meaning alone - and within a tier the same level wins.
Close spelling alone never counts: "evaluation" is not "valuation".

- Similar concept found -> check parents (within each concept's own source):
  - no incoming parent, or a tag (tags are shared keywords) -> skip
  - every incoming parent similar to a parent of the known concept -> skip
  - otherwise -> REVIEW_NEW_PLACEMENT: known concept, new context
    (e.g. "Machine Learning" under Finance vs under Healthcare)
- Nothing similar -> ADD_CANDIDATE when nothing at any level is even
  moderately close, otherwise REVIEW_AMBIGUOUS.

New placements are reported separately and do not count toward the
new-items rate. Every recommendation is a review aid, not a database
operation; flagged entries carry their parents so a downstream integration
team knows where each one hangs off.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

import numpy as np
from rapidfuzz import fuzz, process

from ..datasets.base import LoadedDataset, TaxonomyDataset
from ..models import MetricResult
from .base import Metric
from .integrity import normalize

CONCEPT_LEVELS = ("categories", "specialities", "tags")
SKIP_DUPLICATE = "SKIP_DUPLICATE"
SKIP_SEMANTIC_DUPLICATE = "SKIP_SEMANTIC_DUPLICATE"
REVIEW_NEW_PLACEMENT = "REVIEW_NEW_PLACEMENT"
ADD_CANDIDATE = "ADD_CANDIDATE"
REVIEW_AMBIGUOUS = "REVIEW_AMBIGUOUS"
HAS_PARENT = "HAS_PARENT"
NO_PARENT = "NO_PARENT"
PARENT_LEVEL = {"categories": "services", "specialities": "categories", "tags": "specialities"}

# Copied from (not imported from) `backend/audit/taxonomy_audit.py`, which
# depends on mysql.connector - the same reason integrity.normalize is copied.
PLACEHOLDERS = {
    "arc", "dummy", "eee", "new", "new tag", "placeholder", "sample", "temp", "test",
}

_PLURAL_S = re.compile(r"(?<=\w{3})(?<!s)s\b")


def match_key(name: Any) -> str:
    """Normalized name used for exact matching: `normalize()` plus folding of
    '&'/'and', British/American '-isation', and a trailing plural 's'. Both
    sides of every comparison go through this, so crude plural stripping
    ("analysis" -> "analysi") is harmless as long as it is consistent.
    """
    value = normalize(None if name is None else str(name))
    value = value.replace("&", " and ").replace("isation", "ization")
    value = re.sub(r"\s+", " ", value).strip()
    return _PLURAL_S.sub("", value)


def _is_valid_name(name: Any) -> bool:
    if name is None:
        return False
    normalized = normalize(str(name))
    return any(ch.isalnum() for ch in normalized) and normalized not in PLACEHOLDERS


class SemanticMatcher:
    """Lazy sentence-embedding matcher, kept optional for lightweight runs."""

    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ValueError(
                "semantic matching requires the optional dependency; install with "
                '`pip install -e ".[dev,semantic]"`'
            ) from exc
        self.model = SentenceTransformer(model_name)
        self._cache: dict[str, np.ndarray] = {}

    def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray:
        """Cosine similarity matrix (queries x choices); each distinct name is
        encoded once per run, since the same parent names recur constantly."""
        missing = [name for name in dict.fromkeys(queries + choices) if name not in self._cache]
        if missing:
            for name, vector in zip(missing, self.model.encode(missing, normalize_embeddings=True), strict=True):
                self._cache[name] = np.asarray(vector)
        if not queries or not choices:
            return np.zeros((len(queries), len(choices)))
        return np.stack([self._cache[q] for q in queries]) @ np.stack([self._cache[c] for c in choices]).T

    def best_matches(self, queries: list[str], choices: list[str]) -> list[tuple[str | None, float | None]]:
        if not queries or not choices:
            return [(None, None) for _ in queries]
        embeddings = self.model.encode(queries + choices, normalize_embeddings=True)
        query_vectors = np.asarray(embeddings[: len(queries)])
        choice_vectors = np.asarray(embeddings[len(queries) :])
        scores = query_vectors @ choice_vectors.T
        best_indexes = np.argmax(scores, axis=1)
        return [(choices[index], round(float(scores[row, index]), 4)) for row, index in enumerate(best_indexes)]


def _group_concepts(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Group valid named rows by match key (first-seen order), so a concept
    repeated under several parents counts once. Returns (groups, invalid)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    invalid = []
    for row in rows:
        name = row.get("name")
        if not _is_valid_name(name):
            invalid.append({"incoming_id": row.get("id"), "incoming_name": name})
            continue
        groups.setdefault(match_key(name), []).append(row)
    return groups, invalid


class _Parents:
    """Resolves each row's parents inside its own source dataset only."""

    def __init__(self, dataset: TaxonomyDataset):
        self.services = {row.get("id"): row for row in dataset.services}
        self.categories = {row.get("id"): row for row in dataset.categories}
        self.specialities = {row.get("id"): row for row in dataset.specialities}
        self.tag_specialities: dict[Any, list[Any]] = {}
        for link in dataset.speciality_tags:
            self.tag_specialities.setdefault(link.get("tag_id"), []).append(link.get("speciality_id"))

    def lookup(self, level: str, row: dict[str, Any]) -> list[dict[str, Any] | None]:
        if level == "categories":
            return [self.services.get(row.get("service_id"))]
        if level == "specialities":
            return [self.categories.get(row.get("category_id"))]
        return [self.specialities.get(sid) for sid in self.tag_specialities.get(row.get("id"), [])]

    def classify(self, level: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        parents: dict[Any, dict[str, Any]] = {}
        for row in rows:
            for parent in self.lookup(level, row):
                if parent is not None and parent.get("id") not in parents:
                    parents[parent.get("id")] = {"id": parent.get("id"), "name": parent.get("name")}
        return {
            "parent_status": HAS_PARENT if parents else NO_PARENT,
            "parent_level": PARENT_LEVEL[level],
            "parent_count": len(parents),
            "parents": list(parents.values()),
        }


class _KnownPool:
    """Every concept seen so far (baseline + earlier incoming sources), by level."""

    def __init__(self):
        self.by_level: dict[str, dict[str, dict[str, Any]]] = {level: {} for level in CONCEPT_LEVELS}

    def add(self, source: str, dataset: TaxonomyDataset) -> None:
        """Add a source's concepts. Parents of *every* row sharing a key are
        merged into the known concept - also for keys already known - so a
        placement seen in any earlier source is never flagged again."""
        parents = _Parents(dataset)
        for level in CONCEPT_LEVELS:
            known = self.by_level[level]
            for key, rows in _group_concepts(getattr(dataset, level))[0].items():
                if key not in known:
                    row = rows[0]
                    known[key] = {
                        "source": source, "id": row.get("id"), "name": row.get("name"),
                        "normalized": normalize(str(row.get("name"))), "status": row.get("status"),
                        "parents": {},
                    }
                for row in rows:
                    for parent in parents.lookup(level, row):
                        if parent is not None and _is_valid_name(parent.get("name")):
                            known[key]["parents"].setdefault(match_key(parent["name"]), {
                                "name": parent["name"], "normalized": normalize(str(parent["name"])),
                            })

    def size(self) -> int:
        return sum(len(known) for known in self.by_level.values())

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


def _rate(count: int, total: int) -> float | None:
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

    scores = process.cdist([key for key, _ in concepts], keys, scorer=fuzz.ratio)
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
    set it came from); within a tier the same level wins. The same-level exact
    tier is resolved before evidence is gathered."""
    if other.exact:
        return "exact", other.exact, other
    for evidence in (same, other):
        if evidence.close_spelling(p) and evidence.semantic_agrees(p):
            return "close_spelling", evidence.lexical, evidence
    for evidence in (same, other):
        if evidence.semantic_agrees(p):
            return "semantic", evidence.semantic, evidence
    return None


def _compare_level(
    level: str,
    incoming_rows: list[dict[str, Any]],
    pool: _KnownPool,
    parents: _Parents,
    p: dict[str, Any],
    semantic_matcher: SemanticMatcher | None,
    seen_in_source: dict[str, str],
) -> dict[str, Any]:
    groups, invalid = _group_concepts(incoming_rows)
    # A name already counted at an earlier level of this same source is the
    # same concept; count it once.
    repeated = [
        {"incoming_ids": [r.get("id") for r in rows], "incoming_name": str(rows[0]["name"]),
         "first_counted_level": seen_in_source[key]}
        for key, rows in groups.items() if key in seen_in_source
    ]
    groups = {key: rows for key, rows in groups.items() if key not in seen_in_source}
    seen_in_source.update(dict.fromkeys(groups, level))
    known = pool.by_level[level]

    exact_matches: list[dict[str, Any]] = []
    fuzzy_matches: list[dict[str, Any]] = []
    semantic_matches: list[dict[str, Any]] = []
    new_placements: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    placements_checked = 0

    matched: list[tuple[list[dict[str, Any]], str, tuple[str, dict[str, Any], float], _Evidence | None]] = []
    remaining: list[tuple[str, list[dict[str, Any]]]] = []
    for key, rows in groups.items():
        if key in known:
            matched.append((rows, "exact", (level, known[key], 100.0), None))
        else:
            remaining.append((key, rows))

    same_evidence = _gather_evidence(remaining, pool.candidates(level, other_levels=False), semantic_matcher)
    other_candidates = pool.candidates(level, other_levels=True) if p["cross_level"] else {}
    other_evidence = _gather_evidence(remaining, other_candidates, semantic_matcher)
    for (_, rows), same, other in zip(remaining, same_evidence, other_evidence, strict=True):
        winner = _winning_match(same, other, p)
        if winner is None:
            ambiguous.append(_ambiguous_entry(
                rows[0], [r.get("id") for r in rows], same, other, parents.classify(level, rows), p
            ))
        else:
            matched.append((rows, *winner))

    skips_by_type = {"exact": exact_matches, "close_spelling": fuzzy_matches, "semantic": semantic_matches}
    for rows, match_type, (known_level, known_entry, score), evidence in matched:
        row = rows[0]
        parent_info = parents.classify(level, rows)
        semantic_score = evidence.semantic[2] if evidence and evidence.semantic else None
        extra: dict[str, Any] = {"match_type": match_type}
        if match_type == "semantic":
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

        action = SKIP_SEMANTIC_DUPLICATE if match_type == "semantic" else SKIP_DUPLICATE
        skips_by_type[match_type].append(
            _match_entry(row, known_level, known_entry, action, **extra, parent_check=parent_check)
        )

    reviewed = len(groups)
    add_count = sum(item["recommended_action"] == ADD_CANDIDATE for item in ambiguous)
    review_count = len(ambiguous) - add_count
    flagged = ambiguous + new_placements
    matches = exact_matches + fuzzy_matches + semantic_matches + new_placements
    return {
        "relevant_concepts_reviewed": reviewed,
        "exact_matches": len(exact_matches),
        "fuzzy_candidate_matches": len(fuzzy_matches),
        "semantic_candidate_matches": len(semantic_matches),
        "other_level_matches": sum(entry["baseline_level"] != level for entry in matches),
        "ambiguous": len(ambiguous),
        "ambiguous_rate": _rate(len(ambiguous), reviewed),
        "new_rate_min": _rate(add_count, reviewed),
        "new_rate_max": _rate(add_count + review_count, reviewed),
        "new_placements": len(new_placements),
        "placements_checked": placements_checked,
        "new_placement_rate": _rate(len(new_placements), placements_checked),
        "matched_inactive": sum(_is_inactive(entry) for entry in matches),
        "skipped_invalid": invalid,
        "repeated_in_source_other_level": repeated,
        "recommended_action_counts": {
            SKIP_DUPLICATE: len(exact_matches) + len(fuzzy_matches),
            SKIP_SEMANTIC_DUPLICATE: len(semantic_matches),
            REVIEW_NEW_PLACEMENT: len(new_placements),
            ADD_CANDIDATE: add_count,
            REVIEW_AMBIGUOUS: review_count,
        },
        "parent_status_counts": {
            HAS_PARENT: sum(item["parent_status"] == HAS_PARENT for item in flagged),
            NO_PARENT: sum(item["parent_status"] == NO_PARENT for item in flagged),
        },
        "exact_match_details": exact_matches,
        "fuzzy_match_details": fuzzy_matches,
        "semantic_match_details": semantic_matches,
        "new_placement_details": new_placements,
        "ambiguous_concepts": ambiguous,
    }


def _validated_float(p: dict[str, Any], name: str, low: float, high: float) -> float:
    value = float(p[name])
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite and between {low:g} and {high:g}")
    return value


def _incoming_names(value: Any, baseline: Any) -> list[str]:
    names = [value] if isinstance(value, str) else value
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
        raise ValueError("incoming must be a dataset name or a non-empty list of dataset names")
    if len(set(names)) != len(names):
        raise ValueError(f"incoming lists a dataset more than once: {names}")
    if baseline in names:
        raise ValueError(f"incoming must not include the baseline dataset {baseline!r}")
    return names


class SaturationMetric(Metric):
    id = "saturation"
    description = (
        "New-concept rate of each incoming source, in order, against the baseline plus earlier sources, "
        "matching at every level. Known concepts under a different parent are flagged as new placements; "
        "unknown ones are add candidates or review items, with their parents in their own source."
    )
    default_params = {
        "baseline": None,
        "incoming": None,
        "threshold": 90.0,
        "semantic_model": None,
        "semantic_threshold": 0.7,
        "add_candidate_lexical_threshold": 60.0,
        "add_candidate_semantic_threshold": 0.5,
        "cross_level": True,
    }

    def run(self, datasets: dict[str, LoadedDataset], params: dict[str, Any]) -> MetricResult:
        p = self.resolve_params(params)
        try:
            incoming_names = _incoming_names(p["incoming"], p["baseline"])
        except ValueError as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        roles = {"baseline": p["baseline"], **{f"incoming[{i}]": n for i, n in enumerate(incoming_names)}}
        resolved = self.require_datasets(datasets, roles, {role: TaxonomyDataset for role in roles})
        if isinstance(resolved, MetricResult):
            return resolved

        try:
            settings = {
                "threshold": _validated_float(p, "threshold", 0, 100),
                "semantic_threshold": _validated_float(p, "semantic_threshold", -1, 1),
                "add_candidate_lexical_threshold": _validated_float(p, "add_candidate_lexical_threshold", 0, 100),
                "add_candidate_semantic_threshold": _validated_float(p, "add_candidate_semantic_threshold", -1, 1),
            }
        except (TypeError, ValueError) as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        if not isinstance(p["cross_level"], bool):
            return MetricResult(metric_id=self.id, status="error", summary="cross_level must be true or false")
        settings["cross_level"] = p["cross_level"]

        baseline = resolved["baseline"]
        incoming = [resolved[f"incoming[{i}]"] for i in range(len(incoming_names))]
        for source in incoming:
            if not any(_group_concepts(getattr(source, level))[0] for level in CONCEPT_LEVELS):
                return MetricResult(metric_id=self.id, status="error", summary=(
                    f"{source.name} has no named categories, specialities, or tags to compare; "
                    "check the dataset source or table_map."
                ))
        empty_baseline_levels = [
            level for level in CONCEPT_LEVELS
            if not _group_concepts(getattr(baseline, level))[0]
            and any(_group_concepts(getattr(source, level))[0] for source in incoming)
        ]
        if empty_baseline_levels:
            return MetricResult(metric_id=self.id, status="error", summary=(
                f"{baseline.name} has no named records for {', '.join(empty_baseline_levels)} while "
                "an incoming source does; check the dataset source or table_map."
            ))
        try:
            semantic_matcher = SemanticMatcher(str(p["semantic_model"])) if p["semantic_model"] else None
        except ValueError as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))

        pool = _KnownPool()
        pool.add(baseline.name, baseline)
        steps, curve = [], []
        for number, (name, source) in enumerate(zip(incoming_names, incoming, strict=True), start=1):
            parents = _Parents(source)
            seen_in_source: dict[str, str] = {}
            by_level = {
                level: _compare_level(
                    level, getattr(source, level), pool, parents, settings, semantic_matcher, seen_in_source
                )
                for level in CONCEPT_LEVELS
            }
            pool.add(name, source)
            reviewed = sum(result["relevant_concepts_reviewed"] for result in by_level.values())
            ambiguous = sum(result["ambiguous"] for result in by_level.values())
            adds = sum(result["recommended_action_counts"][ADD_CANDIDATE] for result in by_level.values())
            placements = sum(result["new_placements"] for result in by_level.values())
            checked = sum(result["placements_checked"] for result in by_level.values())
            steps.append({
                "source": name,
                "overall": {
                    "relevant_concepts_reviewed": reviewed,
                    "ambiguous": ambiguous,
                    "ambiguous_rate": _rate(ambiguous, reviewed),
                },
                "by_concept_level": by_level,
            })
            curve.append({
                "step": number,
                "source": name,
                "reviewed": reviewed,
                "add_candidates": adds,
                "review_ambiguous": ambiguous - adds,
                "new_rate_min": _rate(adds, reviewed),
                "new_rate_max": _rate(ambiguous, reviewed),
                "new_placements": placements,
                "new_placement_rate": _rate(placements, checked),
                "cumulative_known_concepts": pool.size(),
            })

        last = curve[-1]
        summary = (
            f"{last['source']} against {baseline.name}"
            f"{' + earlier sources' if len(curve) > 1 else ''}: new-items rate "
            f"{last['new_rate_min']:.1%}-{last['new_rate_max']:.1%} of {last['reviewed']} concepts "
            f"({last['add_candidates']} add candidates, {last['review_ambiguous']} need review); "
            f"{last['new_placements']} known concepts in a new placement."
        )
        if len(curve) > 1:
            summary += " Curve: " + ", ".join(
                f"{point['source']} {point['new_rate_min']:.1%}-{point['new_rate_max']:.1%}" for point in curve
            ) + "."
        return MetricResult(
            metric_id=self.id,
            summary=summary,
            details={
                "baseline": baseline.name,
                "incoming": incoming_names,
                "threshold": settings["threshold"],
                "semantic_model": p["semantic_model"],
                "semantic_threshold": settings["semantic_threshold"] if semantic_matcher else None,
                "add_candidate_lexical_threshold": settings["add_candidate_lexical_threshold"],
                "add_candidate_semantic_threshold": (
                    settings["add_candidate_semantic_threshold"] if semantic_matcher else None
                ),
                "overall": steps[-1]["overall"],
                "by_concept_level": steps[-1]["by_concept_level"],
                "cross_level": p["cross_level"],
                "curve": curve,
                "steps": steps,
            },
        )
