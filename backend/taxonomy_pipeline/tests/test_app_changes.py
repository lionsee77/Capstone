"""CLI and recovery use the same audit contracts as the HTTP reader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from taxonomy_pipeline.app.changes import sync_changes
from taxonomy_pipeline.audit import ChangeEvent, ChangeQuery, read_changes
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
