"""The provider for any source imported with `taxonomy-pipeline import-source`.

`import-source` writes each tag's description from the flat source into a
`<name>_tag_context.json` next to the SQL dump, so one provider serves every
such source: it reads that file instead of knowing where a particular source
keeps its extra data. Pass it with `case-b --provider tag_context --context
<file>`. A source with richer extra data than a description can still get its
own module, like `skillsfuture.py`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from .base import SourceProvider


class TagContextProvider(SourceProvider):
    """Reads per-tag context, and the source's display name, from an `import-source` tag-context file."""

    name: ClassVar[str] = "tag_context"
    display_name: ClassVar[str] = "incoming source"

    def load_tag_context(self, path: str | Path | None) -> dict[str, dict[str, Any]]:
        return {str(tag_id): dict(context) for tag_id, context in _read(path).get("tags", {}).items()}

    def source_name(self, path: str | Path | None) -> str:
        return str(_read(path).get("display_name") or self.display_name)


def _read(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        raise ValueError("the tag_context provider needs --context: the <name>_tag_context.json import-source wrote")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tags", {}), dict):
        raise ValueError(f"{path} is not an import-source tag-context file (expected a 'tags' object)")
    return data
