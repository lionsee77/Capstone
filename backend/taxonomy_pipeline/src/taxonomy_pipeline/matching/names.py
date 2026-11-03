"""Name keys and lexical similarity: how two concept names compare before any model is involved.

Each fold answers a different question, so stages must not swap one for another:

- `normalize`: the same text, ignoring case, spacing and trailing punctuation? Used by
  integrity's duplicate counts; intake, deduplication, resolution and review name
  lookups; `/taxonomy` search.
- `match_key`: the same concept? `normalize` plus '&'/'and', -isation and a trailing
  plural 's'. Used by saturation and Case A (`engine.py`), deduplication, hierarchy
  screening and the preview's moved-placement note.
- `preprocessing.py` profiles: the text breadth embeds or fuzz-matches, versioned with
  the metric definitions. Used by breadth only.
- `collation_key`: would MySQL's `utf8mb4_general_ci` call these equal? Used by
  integrate's check against the client schema's unique `tags.name` and by
  `import-source`'s tag deduplication. Defined in `taxonomy/sql_dump.py`, beside the
  SQL writers, so the taxonomy layer can use it; re-exported here.

- `normalize` must stay step-for-step identical to `backend/audit/taxonomy_audit.py`, or
  integrity's duplicate counts stop matching that audit.
- `is_valid_name` filters placeholders and names with no letters or digits.
- `lexical_scores` is the RapidFuzz 0-100 ratio matrix saturation and screening share.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

import numpy as np
from rapidfuzz import fuzz, process

from ..taxonomy.sql_dump import collation_key  # noqa: F401 - defined beside the SQL writers; re-exported here

_DASH_VARIANTS = re.compile(r"[‐‑‒–—―]")


# Normalize names consistently even when trailing punctuation is separated by whitespace.
def normalize(name: str | None) -> str:
    """Duplicated from (not imported from) `backend/audit/taxonomy_audit.py`'s
    `normalized()`, step for step - that module pulls in `mysql.connector` as
    a live-DB dependency this pipeline deliberately doesn't take on, but the
    steps must still match exactly or duplicate counts stop being comparable
    to that baseline, which is integrity's whole point.
    """
    value = unicodedata.normalize("NFKC", name or "").strip().casefold()
    value = _DASH_VARIANTS.sub("-", value)
    value = re.sub(r"\s+", " ", value)
    return value.rstrip(" .,;:!?")


# Copied from (not imported from) `backend/audit/taxonomy_audit.py`, which
# depends on mysql.connector - the same reason `normalize` above is copied.
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


def is_valid_name(name: Any) -> bool:
    if name is None:
        return False
    normalized = normalize(str(name))
    return any(ch.isalnum() for ch in normalized) and normalized not in PLACEHOLDERS


# Share saturation's 0-100 RapidFuzz ratio and precision with downstream proposal screening.
def lexical_scores(queries: list[str], choices: list[str]) -> np.ndarray:
    return process.cdist(queries, choices, scorer=fuzz.ratio)
