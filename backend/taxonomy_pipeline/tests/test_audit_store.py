"""Audit events survive retries and cannot be rewritten by ordinary application SQL."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest

from taxonomy_pipeline.audit import ChangeQuery, log_change, log_changes, read_changes
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


# Stable IDs make an exact retry a no-op, while altered evidence aborts the whole operation.
def test_retry_is_idempotent_and_conflict_rolls_back_the_batch(audit_event: ChangeEvent) -> None:
    assert log_change(audit_event) == 1
    assert log_change(audit_event) == 0
    other = audit_event.model_copy(update={"event_id": "other"})
    conflict = audit_event.model_copy(update={"reason": "Rewritten history"})
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
