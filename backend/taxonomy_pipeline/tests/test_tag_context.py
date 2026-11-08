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
    path = _context_file(tmp_path, {
        "source": "esco", "display_name": "ESCO", "tags": {"3001": {"description": "Query databases"}},
    })
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


@pytest.mark.parametrize("payload", [{}, {"tags": {"3001": "not an object"}}])
def test_rejects_missing_or_invalid_tag_context_entries(tmp_path: Path, payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        TagContextProvider().load_tag_context(_context_file(tmp_path, payload))


def test_preserves_rich_context_without_source_specific_parsing(tmp_path: Path) -> None:
    context = {"skill_id": "SK-A", "title": "Queries", "description": "Query databases", "type": "Technical"}
    path = _context_file(tmp_path, {"source": "skillsfuture", "display_name": "SkillsFuture", "tags": {"9": context}})
    assert TagContextProvider().load_tag_context(path) == {"9": context}


def test_workbook_path_explains_that_generated_json_is_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a workbook"):
        TagContextProvider().load_tag_context(tmp_path / "skills.xlsx")


def test_rejects_another_sources_context(tmp_path: Path) -> None:
    path = _context_file(tmp_path, {"source": "onet", "tags": {"3001": {"description": "Software"}}})
    with pytest.raises(ValueError, match="does not match incoming"):
        TagContextProvider().load_context(path, "skillsfuture")


def test_context_hash_identifies_the_exact_bytes_read(tmp_path: Path) -> None:
    import hashlib

    path = _context_file(tmp_path, {"source": "skillsfuture", "display_name": "SkillsFuture", "tags": {}})
    snapshot = TagContextProvider().load_context(path, "skillsfuture")
    assert snapshot.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert snapshot.source == "skillsfuture"
    assert snapshot.display_name == "SkillsFuture"
