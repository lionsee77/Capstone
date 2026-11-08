"""The adapter each external source implements, so generic code never names one.

A source's taxonomy loads like any other dataset; what Case B also needs is
context about each incoming tag (a description, a type) that the taxonomy
dump doesn't carry, and the name to call the source by in its prompt. A
provider supplies both. Standard tag-context JSON uses `tag_context` for any
source. For a custom context format, subclass `SourceProvider` in a new module
and register an instance in `registry.py`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar


class SourceProvider(ABC):
    """Translates one external source's extra data into per-tag context for Case B.

    `name` is the registry key (by convention the source's dataset name in the
    pipeline config); `display_name` is how prompts refer to the source.
    """

    name: ClassVar[str] # Registry key
    display_name: ClassVar[str] # Human readable name
    # The context a tag with no entry in the source's extra data gets.
    missing_context: ClassVar[dict[str, Any]] = {}

    def default_context_path(self) -> Path | None:
        """Where the source's extra data lives when the caller doesn't say; None if it has none."""
        return None

    @abstractmethod
    def load_tag_context(self, path: str | Path | None) -> dict[str, dict[str, Any]]:
        """Context for each incoming tag ID (as a string), merged into the tag's entry in the prompt."""
    # Returns a mapping like:
    # {
    # "3001": {
    #     "title": "Query databases",
    #     "description": "Use database query languages",
    #     "type": "Database software",
    #     }
    # }

    # known tag receives its metdata, unknown tags receives the provider's fallback
    # case B uses this when constructing each prompt item
    def context_for(self, contexts: dict[str, dict[str, Any]], tag_id: str) -> dict[str, Any]:
        return contexts.get(tag_id, self.missing_context)

    # overwritten when the source name is stored inside a file
    def source_name(self, path: str | Path | None) -> str:
        """How the prompt names the source; a provider shared by many sources reads it from `path`."""
        return self.display_name
