"""Synonym sources: suggest other names for concepts, which a reviewer then approves or rejects."""
from __future__ import annotations

from .base import Suggestion, SynonymSource
from .registry import SOURCES, get_source

__all__ = ["SOURCES", "Suggestion", "SynonymSource", "get_source"]
