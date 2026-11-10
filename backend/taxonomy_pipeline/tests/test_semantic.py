"""The sentence-model wrappers: however many threads ask, only one is ever inside a model."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from taxonomy_pipeline.matching import embeddings
from taxonomy_pipeline.matching.semantic import SemanticMatcher


class _CountingModel:
    """Records how many threads are inside `encode` at once, as the real model must never see more than one."""

    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.active = 0
        self.peak = 0

    def encode(self, names: list[str], **kwargs: object) -> np.ndarray:
        with self.guard:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.01)
        with self.guard:
            self.active -= 1
        return np.ones((len(names), 2)) / np.sqrt(2)


def test_matchers_on_different_threads_never_encode_at_once() -> None:
    model = _CountingModel()
    matchers = [SemanticMatcher.__new__(SemanticMatcher) for _ in range(4)]
    for index, matcher in enumerate(matchers):
        matcher.model, matcher._cache = model, {}
        matcher.tag = index

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda m: m.similarity([f"query {m.tag}"], [f"choice {m.tag}"]), matchers))

    assert model.peak == 1


def test_breadth_and_a_matcher_share_the_same_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _CountingModel()
    monkeypatch.setattr(embeddings, "_get_model", lambda: model)
    matcher = SemanticMatcher.__new__(SemanticMatcher)
    matcher.model, matcher._cache = model, {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda call: call(), [
            lambda: embeddings.encode_labels(["a", "b"]),
            lambda: matcher.similarity(["c"], ["d"]),
        ]))

    assert model.peak == 1
