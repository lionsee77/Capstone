"""The provider for any source imported with `taxonomy-pipeline import-source`.

`import-source` writes each tag's description from the flat source into a
`<name>_tag_context.json` next to the SQL dump, so one provider serves every
such source: it reads that file instead of knowing where a particular source
keeps its extra data. The standalone SkillsFuture generator writes the same
format, including skill ID and type. Pass it with `case-b --provider
tag_context --context <file>` for either source.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, ClassVar

from .base import SourceProvider, require_context
from .models import TagContext


class TagContextProvider(SourceProvider):
    """Reads per-tag context, and the source's display name, from an `import-source` tag-context file."""

    name: ClassVar[str] = "tag_context"
    display_name: ClassVar[str] = "incoming source"

    def load_tag_context(self, path: str | Path | None) -> dict[str, dict[str, Any]]:
        return _read(path).tags

    def source_name(self, path: str | Path | None) -> str:
        return _read(path).display_name

    def load_context(self, path: str | Path | None, incoming: str) -> TagContext:
        """Read once so source checks, prompt metadata and checkpoint hash describe the same bytes."""
        context = _read(path)
        if context.source != incoming:
            raise ValueError(f"Context source {context.source!r} does not match incoming dataset {incoming!r}")
        return context


def _read(path: str | Path | None) -> TagContext:
    path = require_context(path)
    if Path(path).suffix.lower() in {".xlsx", ".xlsm"}:
        raise ValueError(
            "--context requires generated tag-context JSON, not a workbook; run the source generator first"
        )
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("tags"), dict):
        raise ValueError(f"{path} is not an import-source tag-context file (expected a 'tags' object)")
    if any(not isinstance(context, dict) for context in data["tags"].values()):
        raise ValueError(f"{path}: each tag context must be an object")
    return TagContext(
        source=data.get("source", ""), display_name=data.get("display_name") or data.get("source", ""),
        tags=data["tags"], sha256=hashlib.sha256(raw).hexdigest(),
    )
