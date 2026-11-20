"""Tag lookup use case: does a proposed term already exist in the taxonomy?

Behind `GET /lookup` and `taxonomy-pipeline lookup`, for consumers such as Team 31
who should check a term before proposing it. They have no run ID and shouldn't need
one, so unlike `browse.py` this reads an integrated snapshot directly: the newest
round under the integrations root unless one is named. There is no promotion step
yet, so "newest" is the closest thing to a current taxonomy; the result names
the snapshot so a caller can see which version answered.

A lookup runs on every keystroke of the dashboard's form, so a snapshot's parsed rows
and name folds are kept between calls. They are keyed by file contents, so a new
round, or an edited one, is picked up without a restart.

Approved global synonyms count as exact matches (`match_type: "synonym"`). They
are read live from the synonym store for `synonym_dataset` (default `original`, the
taxonomy synonyms are recorded against), only those whose term key (`match_key`, as
for names) is the query's, through the store's index, so each keystroke reads a few rows.
A snapshot keeps the baseline's IDs, since `integrate` only inserts, but a synonym only
applies where its concept ID still has the name it was recorded with, so a mismatched
snapshot never gets a wrong match.
Scoped synonyms need the branch a term is used in, which a lookup doesn't have, so
they don't apply here; `synonym-resolve --within-...` answers that question.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import DatasetConfig
from ..enrichment.lineage import FileFingerprint, IntegrationRound, read_rounds
from ..matching.lookup import DEFAULT_MIN_SCORE, NameIndex, NameMatch
from ..matching.names import match_key
from ..matching.semantic import SemanticMatcher
from ..models import ConceptLevel, LookupMatch, TagLookupResult
from ..synonyms import Synonym, approved_synonyms
from ..taxonomy import load_dataset
from ..taxonomy.base import Hierarchy, TaxonomyDataset
from .browse import LEVELS, concept_paths

DEFAULT_LIMIT = 20
# The dataset synonyms are recorded against (`synonym-add --dataset`), whose IDs integrated snapshots keep.
DEFAULT_SYNONYM_DATASET = "original"
MAX_LIMIT = 100
MAX_TERM_LENGTH = 200
# The model every semantic stage uses; `SemanticMatcher` loads it at its pinned revision.
SEMANTIC_MODEL = "all-MiniLM-L6-v2"


class NoSnapshots(Exception):
    """No integrated round exists yet, so there is no taxonomy to check against."""


class SnapshotNotFound(Exception):
    """The caller named a snapshot that isn't a readable round."""


class SemanticUnavailable(Exception):
    """Meaning matching was asked for, but its model couldn't be loaded."""


@dataclass(frozen=True)
class _Snapshot:
    """One snapshot's rows with everything a lookup would otherwise recompute."""

    fingerprint: FileFingerprint
    hierarchy: Hierarchy
    rows: list[tuple[ConceptLevel, dict[str, Any]]]
    index: NameIndex
    by_id: dict[tuple[str, str], dict[str, Any]]


# FastAPI runs each request on its own thread, and loading or running the sentence model from two at
# once kills the whole process (abort or segfault, nothing Python can catch), so they take turns.
_SEMANTIC_LOCK = threading.Lock()
# One slot: lookups almost always read the newest snapshot, and a full taxonomy isn't small.
_loaded: _Snapshot | None = None


def lookup_term(
    term: str, integrations_root: Path, *, snapshot: str | None = None, level: ConceptLevel | None = None,
    limit: int = DEFAULT_LIMIT, min_score: float = DEFAULT_MIN_SCORE, semantic: bool = False,
    synonym_dataset: str | None = DEFAULT_SYNONYM_DATASET,
) -> TagLookupResult:
    """Exact and near matches for `term`. `limit` caps near matches only: every exact one is always returned.

    Synonym hits from `synonym_dataset` join the exact matches; None leaves synonyms out."""
    _check_inputs(term, level, limit, min_score)
    chosen = _choose_round(integrations_root, snapshot)
    loaded = _load(chosen)
    if semantic:
        with _SEMANTIC_LOCK:
            matcher = _semantic_matcher()
            matches = loaded.index.match(term, min_score=min_score, matcher=matcher)
            matcher.retain(loaded.index.names)  # past searches and older snapshots shouldn't pile up in memory
    else:
        matches = loaded.index.match(term, min_score=min_score)
    if level:
        matches = [match for match in matches if loaded.rows[match.index][0] == level]
    exact = [_to_model(loaded, match) for match in matches if match.exact]
    if synonym_dataset:
        named = {(match.level, str(match.id)) for match in exact}
        exact += [hit for hit in _synonym_hits(loaded, approved_synonyms(synonym_dataset, term=term), level)
                  if (hit.level, str(hit.id)) not in named]
    found = {(match.level, str(match.id)) for match in exact}  # a concept found exactly isn't also listed as near
    near = [model for model in (_to_model(loaded, match) for match in matches if not match.exact)
            if (model.level, str(model.id)) not in found][:limit]
    return TagLookupResult(query=term, snapshot=chosen.name, exists=bool(exact), exact=exact, near=near)


def _check_inputs(term: str, level: ConceptLevel | None, limit: int, min_score: float) -> None:
    if not any(char.isalnum() for char in term):
        raise ValueError("term must contain a letter or digit")
    if len(term) > MAX_TERM_LENGTH:
        raise ValueError(f"term must be at most {MAX_TERM_LENGTH} characters")
    if level is not None and level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if not 0 <= min_score <= 100:
        raise ValueError("min_score must be between 0 and 100")


def _choose_round(integrations_root: Path, snapshot: str | None) -> IntegrationRound:
    # Names are compared with what's on disk, never joined into a path, so `snapshot` can't escape the root.
    rounds = read_rounds(integrations_root)
    if snapshot is not None:
        for item in rounds:
            if item.name == snapshot:
                return item
        raise SnapshotNotFound(f"no snapshot named {snapshot!r}")
    if not rounds:
        raise NoSnapshots(f"no integrated snapshots under {integrations_root}; run `taxonomy-pipeline integrate` first")
    return max(rounds, key=lambda item: (item.created_at.isoformat() if item.created_at else "", item.name))


def _load(item: IntegrationRound) -> _Snapshot:
    global _loaded
    current = _loaded
    if current is not None and current.fingerprint == item.snapshot:
        return current
    paths = [str(path) for path in item.files]
    loaded = load_dataset(DatasetConfig(name=item.name, paths=paths, table_map=item.table_map))
    if not isinstance(loaded, TaxonomyDataset):  # `DatasetConfig` defaults to a SQL dump; this narrows the type
        raise TypeError(f"snapshot {item.name!r} did not load as a taxonomy")
    rows: list[tuple[ConceptLevel, dict[str, Any]]] = [
        (level, row) for level in LEVELS for row in getattr(loaded, level) if row.get("name") is not None
    ]
    index = NameIndex([str(row["name"]) for _, row in rows])
    by_id = {(level, str(row.get("id"))): row for level, row in rows}
    _loaded = _Snapshot(item.snapshot, loaded.hierarchy(), rows, index, by_id)
    return _loaded


@lru_cache(maxsize=1)
def _load_matcher() -> SemanticMatcher:
    return SemanticMatcher(SEMANTIC_MODEL)


def _semantic_matcher() -> SemanticMatcher:
    """The shared model, loaded on first use; it also keeps the current snapshot's name embeddings."""
    try:
        return _load_matcher()
    except (ValueError, OSError) as exc:  # the optional dependency is missing, or the model can't be fetched
        raise SemanticUnavailable(f"semantic matching is unavailable: {exc}") from exc


def _synonym_hits(loaded: _Snapshot, synonyms: list[Synonym], level: ConceptLevel | None) -> list[LookupMatch]:
    """Concepts the query names through these approved global synonyms (already matched by term key),
    where the snapshot still has the concept with the name the synonym was recorded against."""
    hits = []
    for synonym in synonyms:
        if synonym.scope or (level and synonym.concept_level != level):
            continue
        row = loaded.by_id.get((synonym.concept_level, synonym.concept_id))
        if row is None or match_key(str(row.get("name"))) != match_key(synonym.concept_name):
            continue  # this snapshot doesn't have the concept the synonym was recorded against
        hits.append(LookupMatch(
            id=row["id"], name=str(row["name"]), level=synonym.concept_level, match_type="synonym",
            lexical_score=100.0, synonym=synonym.term, active=str(row.get("status")) != "0",
            paths=concept_paths(loaded.hierarchy, synonym.concept_level, row),
        ))
    return hits


def _to_model(loaded: _Snapshot, match: NameMatch) -> LookupMatch:
    level, row = loaded.rows[match.index]
    return LookupMatch(
        id=row["id"], name=str(row["name"]), level=level, match_type=match.match_type,
        lexical_score=match.lexical_score, semantic_score=match.semantic_score,
        # The client's dumps mark a retired row '0'; a table without the column has none retired.
        active=str(row.get("status")) != "0",
        paths=concept_paths(loaded.hierarchy, level, row),
    )
