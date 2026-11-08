"""Meaning-based similarity: the optional sentence-model matcher and the interface its consumers take.

`SimilarityMatcher` is all deduplication, resolution and screening need, so tests can pass any
object with a `similarity` matrix. `SemanticMatcher` is the real one, loading the model at the
revision pinned in `embeddings.PINNED_REVISIONS` unless told otherwise.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

from . import embeddings


class SimilarityMatcher(Protocol):
    """The matrix interface implemented by SemanticMatcher."""

    # Score each query against every choice without collapsing equal names or source IDs.
    def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray: ...


class SemanticMatcher:
    """Lazy sentence-embedding matcher, kept optional for lightweight runs.

    `revision` pins the model to one Hugging Face commit. Without it, a model
    `embeddings.PINNED_REVISIONS` names loads at that pin (the one breadth
    uses), and any other model at its current default branch, which can move.
    """

    def __init__(self, model_name: str, revision: str | None = None):
        revision = revision or embeddings.pinned_revision(model_name)
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ValueError(
                "semantic matching requires the optional dependency; install with "
                '`pip install -e ".[dev,semantic]"`'
            ) from exc
        self.model = SentenceTransformer(model_name, revision=revision)
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

    def retain(self, names: list[str]) -> None:
        """Forget every embedding except `names`'. A batch run needs no such thing; a long-running
        service does, or each distinct query it is ever asked stays in memory."""
        keep = set(names)
        for name in [name for name in self._cache if name not in keep]:
            del self._cache[name]

    def best_matches(self, queries: list[str], choices: list[str]) -> list[tuple[str | None, float | None]]:
        if not queries or not choices:
            return [(None, None) for _ in queries]
        scores = self.similarity(queries, choices)
        best_indexes = np.argmax(scores, axis=1)
        return [(choices[index], round(float(scores[row, index]), 4)) for row, index in enumerate(best_indexes)]

    def top_matches(self, queries: list[str], choices: list[str], k: int) -> list[list[tuple[str, float]]]:
        """The k closest choices per query, closest first (ties keep choice order)."""
        if not queries or not choices:
            return [[] for _ in queries]
        scores = self.similarity(queries, choices)
        return [
            [(choices[index], round(float(row[index]), 4)) for index in np.argsort(-row, kind="stable")[:k]]
            for row in scores
        ]
