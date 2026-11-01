"""CLI and recovery use the same audit contracts as the HTTP reader."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from taxonomy_pipeline.app import changes as changes_app
from taxonomy_pipeline.app.changes import sync_changes
from taxonomy_pipeline.audit import ChangeEvent, ChangeQuery, read_changes, store
from taxonomy_pipeline.cli import main


# Saved events can be delivered repeatedly without replaying taxonomy operations.
def test_sync_and_cli_history_share_the_persisted_events(
    audit_event: ChangeEvent, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "audit_events.json"
    path.write_text("[" + audit_event.model_dump_json() + "]", encoding="utf-8")
    assert main(["sync-changes", "--events", str(path)]) == 0
    capsys.readouterr()
    assert sync_changes(path) == 0
    assert main(["changes", "--proposal-id", "p1", "--since", "2026-10-30T00:00:00Z"]) == 0
    assert json.loads(capsys.readouterr().out)["changes"][0]["event_id"] == audit_event.event_id
    assert read_changes(ChangeQuery()).total == 1
    assert main(["changes", "--until", "2026-10-30"]) == 1


# A compatible model default must not invalidate saved events or rewrite their original database payloads.
def test_sync_replays_older_events_after_a_defaulted_field_is_added(
    audit_event: ChangeEvent, tmp_path: Path, isolated_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "audit_events.json"
    original_file = "[" + audit_event.model_dump_json() + "]"
    path.write_text(original_file, encoding="utf-8")
    assert sync_changes(path) == 1
    with sqlite3.connect(isolated_db) as connection:
        original_rows = connection.execute("SELECT * FROM taxonomy_change_log").fetchall()

    class ExtendedChangeEvent(ChangeEvent):
        """Simulate a future compatible model without changing production event contracts."""

        audit_format_version: int = 1

    monkeypatch.setattr(changes_app, "ChangeEvent", ExtendedChangeEvent)
    monkeypatch.setattr(store, "ChangeEvent", ExtendedChangeEvent)
    assert sync_changes(path) == 0

    with sqlite3.connect(isolated_db) as connection:
        assert connection.execute("SELECT * FROM taxonomy_change_log").fetchall() == original_rows
    assert path.read_text(encoding="utf-8") == original_file


# Removing internal model round trips must not weaken validation of external event files.
def test_invalid_saved_event_is_rejected_before_database_initialization(
    audit_event: ChangeEvent, tmp_path: Path, isolated_db: Path,
) -> None:
    path = tmp_path / "invalid-events.json"
    path.write_text(json.dumps([audit_event.model_dump(mode="json") | {"actor": " "}]), encoding="utf-8")
    with pytest.raises(ValueError):
        sync_changes(path)
    assert not isolated_db.exists()
