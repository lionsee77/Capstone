"""SQLite append-only storage alongside run history, with genuinely read-only queries.

Writers use one transaction per operation. Stable event IDs make durable event
files replayable after a storage failure; conflicting reuse of an ID is rejected.
No application API updates or deletes history. Triggers also reject SQL updates
and deletes; this is not tamper-proof storage against a database administrator.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from .. import db
from .models import ChangeEvent, ChangeHistory, ChangeQuery

SCHEMA = """
CREATE TABLE IF NOT EXISTS taxonomy_change_log (
    event_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    dataset TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    concept_level TEXT NOT NULL,
    concept_id TEXT,
    concept_ref TEXT NOT NULL,
    action TEXT NOT NULL,
    proposal_namespace TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS change_concept ON taxonomy_change_log(dataset, concept_level, concept_id);
CREATE INDEX IF NOT EXISTS change_date ON taxonomy_change_log(occurred_at, event_id);
CREATE TRIGGER IF NOT EXISTS change_no_update BEFORE UPDATE ON taxonomy_change_log
BEGIN SELECT RAISE(ABORT, 'audit history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_no_delete BEFORE DELETE ON taxonomy_change_log
BEGIN SELECT RAISE(ABORT, 'audit history is append-only'); END;
"""


# Insert one validated event through the shared writer, optionally in an operation's transaction.
def log_change(
    event: ChangeEvent, *, db_path: Path | str | None = None, connection: sqlite3.Connection | None = None,
) -> int:
    if connection is None:
        return log_changes([event], db_path=db_path)
    event = ChangeEvent.model_validate_json(event.model_dump_json())
    payload = json.dumps(event.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    existing = connection.execute("SELECT payload FROM taxonomy_change_log WHERE event_id = ?", (event.event_id,))
    row = existing.fetchone()
    if row is not None:
        if row[0] != payload:
            raise ValueError(f"Conflicting audit event ID: {event.event_id}")
        return 0
    connection.execute(
        "INSERT INTO taxonomy_change_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event.event_id, event.occurred_at.isoformat(timespec="microseconds"), event.dataset, event.snapshot_id,
         event.concept_level, event.concept_id, event.concept_ref, event.action, event.proposal_namespace, payload),
    )
    return 1


# Atomically append all events for one operation; retries preserve the original rows.
def log_changes(events: Sequence[ChangeEvent], *, db_path: Path | str | None = None) -> int:
    if not events:
        return 0
    path = Path(db_path or db.DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        connection.executescript(SCHEMA)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            return sum(log_change(event, connection=connection) for event in events)


# Read history using SQLite read-only mode without creating a database or loading taxonomy files.
def read_changes(query: ChangeQuery, *, db_path: Path | str | None = None) -> ChangeHistory:
    query = ChangeQuery.model_validate(query.model_dump())
    result = ChangeHistory(total=0, offset=query.offset, limit=query.limit, changes=[])
    path = Path(db_path or db.DEFAULT_DB_PATH).resolve()
    if not path.exists():
        return result
    clauses: list[str] = []
    params: list[Any] = []
    for field in ("dataset", "snapshot_id", "concept_level", "concept_id", "concept_ref", "action",
                  "proposal_namespace"):
        value = getattr(query, field)
        if value is not None:
            clauses.append(f"{field} = ?")
            params.append(value)
    for field, array in (("proposal_id", "proposal_ids"), ("decision_id", "decision_ids")):
        value = getattr(query, field)
        if value is not None:
            clauses.append(f"EXISTS (SELECT 1 FROM json_each(payload, '$.{array}') WHERE value = ?)")
            params.append(value)
    for value, operator in ((query.since, ">="), (query.until, "<=")):
        if value is not None:
            clauses.append(f"occurred_at {operator} ?")
            params.append(value.isoformat(timespec="microseconds"))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='taxonomy_change_log'",
        ).fetchone():
            return result
        result.total = connection.execute("SELECT COUNT(*) FROM taxonomy_change_log" + where, params).fetchone()[0]
        rows = connection.execute(
            "SELECT payload FROM taxonomy_change_log" + where + " ORDER BY occurred_at, event_id LIMIT ? OFFSET ?",
            [*params, query.limit, query.offset],
        )
        result.changes = [ChangeEvent.model_validate_json(row[0]) for row in rows]
    return result
