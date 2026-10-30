"""Audit delivery recovery writes history without rerunning taxonomy operations."""
from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter

from ..audit import ChangeEvent, log_changes


# Retry delivery of durable events without rerunning the operation that produced them.
def sync_changes(events_path: str | Path) -> int:
    events = TypeAdapter(list[ChangeEvent]).validate_json(Path(events_path).read_bytes())
    return log_changes(events)
