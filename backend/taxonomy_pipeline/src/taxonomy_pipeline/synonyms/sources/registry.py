"""Synonym sources by id, for `synonym-suggest --source` and `POST /synonyms/suggest`. Add new ones here."""
from __future__ import annotations

from .base import SynonymSource
from .esco import EscoSource
from .onet import OnetSource
from .spelling import SpellingSource

SOURCES: dict[str, SynonymSource] = {source.id: source for source in (SpellingSource(), EscoSource(), OnetSource())}


def get_source(source_id: str) -> SynonymSource:
    try:
        return SOURCES[source_id]
    except KeyError as exc:
        raise ValueError(f"unknown synonym source {source_id!r}; available: {sorted(SOURCES)}") from exc
