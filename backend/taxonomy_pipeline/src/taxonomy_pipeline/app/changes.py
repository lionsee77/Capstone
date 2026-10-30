"""Audit use cases shared by CLI and API; replay writes history, never taxonomy rows."""
from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter

from ..audit import ChangeEvent, ChangeHistory, ChangeQuery, log_changes, read_changes


# Return a validated, paginated view without changing the database or taxonomy artifacts.
def changes(query: ChangeQuery) -> ChangeHistory:
    return read_changes(query)


# Retry delivery of durable events without rerunning the operation that produced them.
def sync_changes(events_path: str | Path) -> int:
    events = TypeAdapter(list[ChangeEvent]).validate_json(Path(events_path).read_bytes())
    return log_changes(events)
