"""Recovery delivers saved audit events without repeating taxonomy operations."""
from __future__ import annotations

from pathlib import Path

import pytest

from taxonomy_pipeline.app.changes import sync_changes
from taxonomy_pipeline.audit import ChangeEvent, ChangeQuery, read_changes
from taxonomy_pipeline.cli import main


# Saved events can be delivered repeatedly without replaying taxonomy operations.
def test_sync_replays_the_persisted_events_once(
    audit_event: ChangeEvent, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "audit_events.json"
    path.write_text("[" + audit_event.model_dump_json() + "]", encoding="utf-8")
    assert main(["sync-changes", "--events", str(path)]) == 0
    capsys.readouterr()
    assert sync_changes(path) == 0
    assert read_changes(ChangeQuery()).total == 1
