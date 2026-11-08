"""The adapter each external source implements, so generic code never names one.

A source's taxonomy loads like any other dataset; what Case B also needs is
context about each incoming tag (a description, a type) that the taxonomy
dump doesn't carry, and the name to call the source by in its prompt. A
provider supplies both. Standard tag-context JSON uses `tag_context` for any
source. For a custom context format, subclass `SourceProvider` in a new module
and register an instance in `registry.py`.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from .models import TagContext


def require_context(path: str | Path | None) -> Path:
    """Fail before dataset loading when the required context argument is absent."""
    if path is None:
        raise ValueError(
            "Case B needs --context with generated tag-context JSON. For SkillsFuture run "
            "`python skillsfuture/generate_skillsfuture_taxonomy.py` from backend/; "
            "for other sources run `taxonomy-pipeline import-source`."
        )
    return Path(path)


class SourceProvider(ABC):
    """Translates one external source's extra data into per-tag context for Case B.

    `name` is the registry key (by convention the source's dataset name in the
    pipeline config); `display_name` is how prompts refer to the source.
    """

    name: ClassVar[str]
    display_name: ClassVar[str]

    @abstractmethod
    def load_tag_context(self, path: str | Path | None) -> dict[str, dict[str, Any]]:
        """Context for each incoming tag ID (as a string), merged into the tag's entry in the prompt."""
    def context_for(self, contexts: dict[str, dict[str, Any]], tag_id: str) -> dict[str, Any]:
        """Missing context leaves a tag eligible for name-only classification."""
        return contexts.get(tag_id, {})

    def source_name(self, path: str | Path | None) -> str:
        """How the prompt names the source; a provider shared by many sources reads it from `path`."""
        return self.display_name

    def load_context(self, path: str | Path | None, incoming: str) -> TagContext:
        """Custom providers may override this to validate their source and read a single snapshot."""
        selected = require_context(path)
        return TagContext(
            source=incoming, display_name=self.source_name(selected), tags=self.load_tag_context(selected),
            sha256=hashlib.sha256(selected.read_bytes()).hexdigest(),
        )
