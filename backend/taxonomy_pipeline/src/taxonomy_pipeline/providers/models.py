"""One validated context snapshot and its provenance, shared by providers and Case B."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TagContext(BaseModel):
    """Bind the exact bytes read to the metadata used for a classification run."""

    source: str = Field(pattern=r"\S")
    display_name: str = Field(pattern=r"\S")
    tags: dict[str, dict[str, Any]]
    sha256: str
