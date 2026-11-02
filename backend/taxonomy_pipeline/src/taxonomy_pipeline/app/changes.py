"""Audit use cases shared by CLI and API; replay writes history, never taxonomy rows."""
from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter

from ..audit import ChangeEvent, ChangeHistory, ChangeQuery, log_changes, read_changes


# Return a validated, paginated view without changing the database or taxonomy artifacts.
def changes(query: ChangeQuery) -> ChangeHistory:
    """Read one history page using the shared filter contract, without loading taxonomy data."""
    return read_changes(query)


# Retry delivery of durable events without rerunning the operation that produced them.
def sync_changes(events_path: str | Path) -> int:
    """Validate a durable event file and return the number of newly inserted events.

    Equivalent replays return zero. Invalid files and conflicting event IDs fail
    without partial insertion; neither the source file nor taxonomy is rewritten.
    """
    events = TypeAdapter(list[ChangeEvent]).validate_json(Path(events_path).read_bytes())
    return log_changes(events)
