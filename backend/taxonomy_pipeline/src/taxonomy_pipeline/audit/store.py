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
SCHEMA_VERSION = 1


# Read the audit context's own version without claiming the shared database's global user_version.
def _schema_version(connection: sqlite3.Connection) -> int:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='taxonomy_audit_schema'",
    ).fetchone():
        return 0
    row = connection.execute("SELECT version FROM taxonomy_audit_schema WHERE singleton = 1").fetchone()
    return row[0] if row else 0


# Initialize an unversioned database once under a writer lock, including existing audit-only databases.
def _ensure_schema(connection: sqlite3.Connection) -> None:
    version = _schema_version(connection)
    if version == SCHEMA_VERSION:
        return
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        version = _schema_version(connection)
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            raise ValueError(f"Unsupported audit schema version {version}; expected {SCHEMA_VERSION}")
        # execute preserves this transaction; executescript would commit before running the schema.
        statement = ""
        for line in SCHEMA.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement = ""
        if statement.strip():
            raise ValueError("Incomplete audit schema statement")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS taxonomy_audit_schema "
            "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)",
        )
        connection.execute("INSERT INTO taxonomy_audit_schema (singleton, version) VALUES (1, ?)", (SCHEMA_VERSION,))


# Insert one validated event through the shared writer, optionally in an operation's transaction.
def log_change(
    event: ChangeEvent, *, db_path: Path | str | None = None, connection: sqlite3.Connection | None = None,
) -> int:
    """Return 1 for an inserted event or 0 for an equivalent replay; reject conflicting IDs.

    A supplied connection must already have the audit schema and an active write
    transaction. Its owner controls commit/rollback. Without one, this function
    delegates to the batch writer, which owns initialization and the transaction.
    """
    if connection is None:
        return log_changes([event], db_path=db_path)
    payload = json.dumps(event.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    existing = connection.execute("SELECT payload FROM taxonomy_change_log WHERE event_id = ?", (event.event_id,))
    row = existing.fetchone()
    if row is not None:
        if ChangeEvent.model_validate_json(row[0]) != event:
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
    """Return the number inserted, rolling back the entire batch on any failure.

    Inputs must already be validated ChangeEvents. Empty batches do not touch the
    filesystem; nonempty batches initialize the audit schema if needed and acquire
    the writer lock before checking replay IDs, so concurrent retries cannot race.
    """
    if not events:
        return 0
    path = Path(db_path or db.DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        _ensure_schema(connection)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            return sum(log_change(event, connection=connection) for event in events)


# Read history using SQLite read-only mode without creating a database or loading taxonomy files.
def read_changes(query: ChangeQuery, *, db_path: Path | str | None = None) -> ChangeHistory:
    """Return a consistent count and page from one read-only SQLite transaction.

    A missing database or audit table yields empty history without creating it.
    Filters are ANDed; concept IDs need dataset, snapshot and level filters to
    identify one concept. Results are ordered by UTC timestamp and event ID.
    """
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
