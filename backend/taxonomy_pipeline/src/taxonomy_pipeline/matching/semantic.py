"""Share the optional similarity interface without coupling its consumers."""
from __future__ import annotations

from typing import Protocol

import numpy as np


class SimilarityMatcher(Protocol):
    """The matrix interface already implemented by saturation.SemanticMatcher."""

    # Score each query against every choice without collapsing equal names or source IDs.
    def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray: ...
