"""Audit events survive retries and cannot be rewritten by ordinary application SQL."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

import pytest

from taxonomy_pipeline.audit import ChangeQuery, log_change, log_changes, read_changes, store
from taxonomy_pipeline.audit.models import ChangeAction, ChangeEvent


@pytest.mark.parametrize("action", get_args(ChangeAction))
# Every governance action retains its affected concept, explanation, time and origin.
def test_records_each_governance_action(action: str, audit_event: ChangeEvent) -> None:
    event = audit_event.model_copy(update={
        "action": action, "scope": "review" if action == "review_decision" else "snapshot",
        "decision": {"action": "reuse"} if action == "review_decision" else None,
    })
    assert log_change(event) == 1
    assert read_changes(ChangeQuery()).changes == [event]


@pytest.mark.parametrize("changed_fields", [
    {"reason": "Rewritten history"},
    {"actor": "another-reviewer"},
    {"occurred_at": datetime(2026, 10, 31, tzinfo=UTC)},
    {"concept_id": "2"},
    {"after": {"id": 1, "name": "Changed concept"}},
])
# Stable IDs make an exact retry a no-op, while altered evidence aborts the whole operation.
def test_retry_is_idempotent_and_conflict_rolls_back_the_batch(
    audit_event: ChangeEvent, changed_fields: dict[str, Any],
) -> None:
    assert log_change(audit_event) == 1
    assert log_change(audit_event) == 0
    other = audit_event.model_copy(update={"event_id": "other"})
    conflict = audit_event.model_copy(update=changed_fields)
    with pytest.raises(ValueError, match="Conflicting"):
        log_changes([other, conflict])
    assert read_changes(ChangeQuery()).changes == [audit_event]


# Concurrent delivery of one durable event must create one row, not two.
def test_concurrent_retries_record_one_event(audit_event: ChangeEvent) -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(log_change, [audit_event, audit_event]))
    assert sorted(results) == [0, 1]
    assert read_changes(ChangeQuery()).total == 1


@pytest.mark.parametrize("statement", [
    "UPDATE taxonomy_change_log SET action = 'remove'",
    "DELETE FROM taxonomy_change_log",
])
# Database triggers enforce append-only behavior even if application code issues raw SQL.
def test_sql_cannot_update_or_delete_events(statement: str, audit_event: ChangeEvent, isolated_db: Path) -> None:
    log_change(audit_event)
    with sqlite3.connect(isolated_db) as connection, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(statement)
    assert read_changes(ChangeQuery()).changes == [audit_event]


# All filters combine, date boundaries include equal instants, and pagination keeps a stable order.
def test_filters_and_paginates_all_changes_for_a_proposal(audit_event: ChangeEvent) -> None:
    later = audit_event.model_copy(update={
        "event_id": "later", "concept_id": "2", "concept_ref": "category:2",
        "occurred_at": datetime(2026, 10, 30, 2, tzinfo=UTC),
    })
    unrelated = audit_event.model_copy(update={"event_id": "unrelated", "proposal_ids": ["elsewhere"]})
    log_changes([later, unrelated, audit_event])
    result = read_changes(ChangeQuery(proposal_id="p1", limit=1))
    assert result.total == 2 and result.changes == [audit_event]
    assert read_changes(ChangeQuery(proposal_id="p1", offset=1)).changes == [later]
    result = read_changes(ChangeQuery(
        dataset="baseline", snapshot_id="snapshot", concept_level="category", concept_id="1",
        concept_ref="category:1", action="add", proposal_id="p1", proposal_namespace="pipeline", decision_id="d1",
        since="2026-10-30T08:00:00+08:00", until="2026-10-30T00:00:00Z",
    ))
    assert result.changes == [audit_event]
    assert read_changes(ChangeQuery(proposal_id="p1' OR 1=1 --")).total == 0
    assert read_changes(ChangeQuery(dataset="other")).total == 0


# Empty history reads do not create storage or migrate an existing run-only database.
def test_reading_missing_history_does_not_write(isolated_db: Path) -> None:
    assert read_changes(ChangeQuery()).total == 0
    assert not isolated_db.exists()
    with sqlite3.connect(isolated_db) as connection:
        connection.execute("CREATE TABLE legacy_runs (id TEXT)")
    before = isolated_db.read_bytes()
    assert read_changes(ChangeQuery()).total == 0
    assert isolated_db.read_bytes() == before


# Each database initializes once; later batches use its persistent marker without executing DDL.
def test_schema_initialization_is_per_database_and_not_per_write(
    audit_event: ChangeEvent, isolated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []
    connect = sqlite3.connect

    # Observe SQL without changing transaction or connection behavior.
    def traced_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store.sqlite3, "connect", traced_connect)
    assert log_change(audit_event) == 1
    assert any("CREATE TABLE IF NOT EXISTS taxonomy_change_log" in statement for statement in statements)
    statements.clear()
    assert log_change(audit_event.model_copy(update={"event_id": "second"})) == 1
    assert not any(statement.lstrip().upper().startswith(("CREATE", "ALTER")) for statement in statements)
    with closing(sqlite3.connect(isolated_db)) as connection:
        assert connection.execute("SELECT version FROM taxonomy_audit_schema").fetchone() == (1,)
    statements.clear()
    assert log_change(audit_event, db_path=tmp_path / "another.sqlite3") == 1
    assert any("CREATE TABLE IF NOT EXISTS taxonomy_change_log" in statement for statement in statements)
    # Replacing a database at the same path must not be mistaken for an initialized cached connection.
    isolated_db.unlink()
    assert log_change(audit_event) == 1
    assert read_changes(ChangeQuery()).total == 1


# Adopting pre-versioning audit tables preserves their payloads and coexisting run-history data.
def test_existing_audit_database_is_versioned_without_rewriting_history(
    audit_event: ChangeEvent, isolated_db: Path,
) -> None:
    with sqlite3.connect(isolated_db) as connection:
        connection.executescript(store.SCHEMA)
        connection.execute("CREATE TABLE legacy_runs (id TEXT)")
        connection.execute("INSERT INTO legacy_runs VALUES ('run1')")
        assert log_change(audit_event, connection=connection) == 1
        original = connection.execute("SELECT * FROM taxonomy_change_log").fetchall()
    assert log_change(audit_event) == 0
    with sqlite3.connect(isolated_db) as connection:
        assert connection.execute("SELECT * FROM taxonomy_change_log").fetchall() == original
        assert connection.execute("SELECT * FROM legacy_runs").fetchall() == [("run1",)]
        assert connection.execute("SELECT version FROM taxonomy_audit_schema").fetchone() == (1,)


# Interrupted schema initialization rolls back its DDL and can be retried without a partial marker.
def test_schema_initialization_failure_rolls_back_and_can_be_retried(
    audit_event: ChangeEvent, isolated_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(store, "SCHEMA", store.SCHEMA + "CREATE TABLE unfinished (")
        with pytest.raises(ValueError, match="Incomplete audit schema"):
            log_change(audit_event)
    with sqlite3.connect(isolated_db) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'taxonomy_%'").fetchall() == []
    assert log_change(audit_event) == 1


# An older application must not silently write to a database using an unsupported audit schema.
def test_future_schema_version_is_rejected_without_changing_events(
    audit_event: ChangeEvent, isolated_db: Path,
) -> None:
    log_change(audit_event)
    with sqlite3.connect(isolated_db) as connection:
        connection.execute("UPDATE taxonomy_audit_schema SET version = 2")
    before = isolated_db.read_bytes()
    with pytest.raises(ValueError, match="Unsupported audit schema version 2"):
        log_change(audit_event.model_copy(update={"event_id": "new"}))
    assert isolated_db.read_bytes() == before
