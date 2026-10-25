"""Versioned, typography-only normalization for breadth scoring inputs."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

_TYPOGRAPHY = str.maketrans({
    **{char: "'" for char in "‘’‚‛"},
    **{char: '"' for char in "“”„‟"},
    **{chr(code): "-" for code in range(0x2010, 0x2016)},
})


def legacy_normalize(name: str | None) -> str:
    """Preserve original breadth normalization independently of other metrics."""
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def typography_v1(name: str | None) -> str:
    """Standardize formatting without removing words or punctuation structure."""
    text = unicodedata.normalize("NFKC", name or "").casefold().translate(_TYPOGRAPHY)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*([/\-&])\s*", r"\1", text)
    text = re.sub(r"([\[(])\s+", r"\1", text)
    text = re.sub(r"\s+([\])])", r"\1", text)
    text = re.sub(r"\s*([,:;])\s*", r"\1 ", text)
    text = re.sub(r"([,:;]) +(?=[\])}])", r"\1", text)
    return text.rstrip()


def get_normalizer(profile: str) -> Callable[[str | None], str]:
    if profile == "typography_v1":
        return typography_v1
    if profile == "legacy":
        return legacy_normalize
    raise ValueError("preprocessing must be 'typography_v1' or 'legacy'")
