"""The tag_context provider: Case B context and source name for any `import-source` source."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from taxonomy_pipeline.providers.registry import get_provider
from taxonomy_pipeline.providers.tag_context import TagContextProvider


def _context_file(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "demo_tag_context.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_reads_each_tags_context_and_the_sources_display_name(tmp_path: Path) -> None:
    path = _context_file(tmp_path, {"display_name": "ESCO", "tags": {"3001": {"description": "Query databases"}}})
    provider = get_provider("tag_context")

    contexts = provider.load_tag_context(path)
    assert isinstance(provider, TagContextProvider)
    assert provider.context_for(contexts, "3001") == {"description": "Query databases"}
    assert provider.context_for(contexts, "3002") == {}
    assert provider.source_name(path) == "ESCO"


def test_needs_a_context_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs --context"):
        TagContextProvider().load_tag_context(None)


def test_refuses_a_file_that_is_not_a_tag_context(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not an import-source tag-context file"):
        TagContextProvider().load_tag_context(_context_file(tmp_path, {"tags": ["3001"]}))
