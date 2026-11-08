"""SQLite storage for synonyms, in the same pipeline-owned database as run history and the audit log.

Each add or remove commits together with its audit event (`synonym_add` /
`synonym_remove`), so the table and its history can't disagree. Rows are never
deleted: removing a synonym marks it `removed`, which keeps who added it and why.
A partial unique index allows one approved mapping per normalised term and
concept; a removed mapping can be added again as a new row.

Nothing here writes to taxonomy files or the client's database. The schema is
versioned in `taxonomy_synonym_schema` (as the audit log versions its own), so
scoping (#61) can migrate it.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import db
from ..audit import ChangeEvent, ensure_schema, log_change
from ..audit.models import utc_now
from ..matching.names import normalize
from ..taxonomy.base import TaxonomyDataset
from .models import AUDIT_LEVELS, Synonym, SynonymCreate, SynonymLevel, SynonymList, SynonymQuery

SCHEMA_VERSION = 1
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS taxonomy_synonyms (
        synonym_id TEXT PRIMARY KEY,
        term TEXT NOT NULL,
        normalized_term TEXT NOT NULL,
        dataset TEXT NOT NULL,
        concept_level TEXT NOT NULL,
        concept_id TEXT NOT NULL,
        concept_name TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        status TEXT NOT NULL,
        source TEXT NOT NULL,
        reason TEXT NOT NULL,
        actor TEXT NOT NULL,
        created_at TEXT NOT NULL,
        removed_at TEXT,
        removed_by TEXT,
        removal_reason TEXT
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS synonym_approved_mapping
        ON taxonomy_synonyms(dataset, concept_level, concept_id, normalized_term) WHERE status = 'approved'""",
    "CREATE INDEX IF NOT EXISTS synonym_term ON taxonomy_synonyms(dataset, normalized_term)",
    """CREATE TABLE IF NOT EXISTS taxonomy_synonym_schema (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL
    )""",
)
COLUMNS = tuple(Synonym.model_fields)


class SynonymError(ValueError):
    """A synonym request that can't be applied; the message says why."""


class UnknownConcept(SynonymError):
    """The concept a synonym would point at doesn't exist in the taxonomy it was checked against."""


class DuplicateSynonym(SynonymError):
    """The same term is already an approved synonym of that concept."""


class SynonymNotFound(SynonymError):
    """No approved synonym has that ID."""


def concept_name(taxonomy: TaxonomyDataset, level: SynonymLevel, concept_id: str) -> str:
    """The canonical name of one concept, or UnknownConcept. IDs compare as strings, as the audit log stores them."""
    row = next((row for row in getattr(taxonomy, level) if str(row.get("id")) == concept_id), None)
    if row is None or not str(row.get("name") or "").strip():
        raise UnknownConcept(f"{taxonomy.name} has no {AUDIT_LEVELS[level]} with ID {concept_id}")
    return str(row["name"])


def add_synonym(
    request: SynonymCreate, *, canonical_name: str, snapshot_id: str, db_path: Path | str | None = None,
) -> Synonym:
    """Store one approved synonym and its `synonym_add` audit event in one transaction.

    `canonical_name` and `snapshot_id` come from the taxonomy the caller checked the
    concept against (`concept_name`). A term equal to that name, or one that is
    already an approved synonym of the concept, is refused.
    """
    normalized = normalize(request.term)
    if not normalized:
        raise SynonymError(f"synonym {request.term!r} has no letters or digits left after normalising")
    if normalized == normalize(canonical_name):
        raise SynonymError(f"{request.term!r} is the concept's own name ({canonical_name!r}), not a synonym")
    synonym = Synonym(
        synonym_id=str(uuid4()), term=request.term, normalized_term=normalized, dataset=request.dataset,
        concept_level=request.concept_level, concept_id=request.concept_id, concept_name=canonical_name,
        snapshot_id=snapshot_id, status="approved", source=request.source, reason=request.reason,
        actor=request.actor, created_at=utc_now(),
    )
    with _writer(db_path) as connection:
        existing = connection.execute(
            "SELECT synonym_id FROM taxonomy_synonyms WHERE status = 'approved' AND dataset = ? "
            "AND concept_level = ? AND concept_id = ? AND normalized_term = ?",
            (synonym.dataset, synonym.concept_level, synonym.concept_id, normalized),
        ).fetchone()
        if existing:
            raise DuplicateSynonym(
                f"{request.term!r} is already an approved synonym of {canonical_name!r} (synonym {existing[0]})"
            )
        connection.execute(
            f"INSERT INTO taxonomy_synonyms ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
            _row(synonym),
        )
        log_change(_event(synonym, "synonym_add", after=synonym), connection=connection)
    return synonym


def remove_synonym(
    synonym_id: str, *, reason: str, actor: str = "unspecified", db_path: Path | str | None = None,
) -> Synonym:
    """Mark one approved synonym `removed` and log `synonym_remove`; the row and its history are kept."""
    with _writer(db_path) as connection:
        row = connection.execute(
            f"SELECT {', '.join(COLUMNS)} FROM taxonomy_synonyms WHERE synonym_id = ? AND status = 'approved'",
            (synonym_id,),
        ).fetchone()
        if row is None:
            raise SynonymNotFound(f"no approved synonym with ID {synonym_id}")
        before = _synonym(row)
        after = before.model_copy(update={
            "status": "removed", "removed_at": utc_now(), "removed_by": actor, "removal_reason": reason,
        })
        after = Synonym.model_validate(after.model_dump())  # re-validate the blank-text rules on reason/actor
        connection.execute(
            "UPDATE taxonomy_synonyms SET status = ?, removed_at = ?, removed_by = ?, removal_reason = ? "
            "WHERE synonym_id = ?",
            (after.status, _time(after.removed_at), after.removed_by, after.removal_reason, synonym_id),
        )
        log_change(_event(after, "synonym_remove", before=before, after=after), connection=connection)
    return after


def list_synonyms(query: SynonymQuery, *, db_path: Path | str | None = None) -> SynonymList:
    """One page of synonyms, oldest first, read without creating or migrating the database."""
    result = SynonymList(total=0, offset=query.offset, limit=query.limit, synonyms=[])
    path = Path(db_path or db.DEFAULT_DB_PATH).resolve()
    if not path.exists():
        return result
    clauses, params = [], []
    for field in ("dataset", "concept_level", "concept_id"):
        value = getattr(query, field)
        if value is not None:
            clauses.append(f"{field} = ?")
            params.append(value)
    if query.term is not None:
        clauses.append("normalized_term = ?")
        params.append(normalize(query.term))
    if not query.include_removed:
        clauses.append("status = 'approved'")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='taxonomy_synonyms'",
        ).fetchone():
            return result
        result.total = connection.execute("SELECT COUNT(*) FROM taxonomy_synonyms" + where, params).fetchone()[0]
        rows = connection.execute(
            f"SELECT {', '.join(COLUMNS)} FROM taxonomy_synonyms{where} "
            "ORDER BY created_at, synonym_id LIMIT ? OFFSET ?",
            [*params, query.limit, query.offset],
        )
        result.synonyms = [_synonym(row) for row in rows]
    return result


class _writer:
    """One `BEGIN IMMEDIATE` transaction with both schemas in place; commits on success, rolls back on error."""

    def __init__(self, db_path: Path | str | None):
        path = Path(db_path or db.DEFAULT_DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)

    def __enter__(self) -> sqlite3.Connection:
        try:
            ensure_schema(self.connection)
            self.connection.execute("BEGIN IMMEDIATE")
            _ensure_schema(self.connection)
        except BaseException:
            self.connection.close()
            raise
        return self.connection

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the tables inside the caller's write transaction, or refuse a schema from another version."""
    for statement in SCHEMA:
        connection.execute(statement)
    row = connection.execute("SELECT version FROM taxonomy_synonym_schema WHERE singleton = 1").fetchone()
    if row is None:
        connection.execute("INSERT INTO taxonomy_synonym_schema (singleton, version) VALUES (1, ?)", (SCHEMA_VERSION,))
    elif row[0] != SCHEMA_VERSION:
        raise SynonymError(f"Unsupported synonym schema version {row[0]}; expected {SCHEMA_VERSION}")


def _event(synonym: Synonym, action: str, *, before: Synonym | None = None, after: Synonym) -> ChangeEvent:
    level = AUDIT_LEVELS[synonym.concept_level]
    operation = f"synonym:{synonym.synonym_id}:{action.removeprefix('synonym_')}"
    return ChangeEvent(
        event_id=operation, operation_id=operation, dataset=synonym.dataset, snapshot_id=synonym.snapshot_id,
        concept_level=level, concept_id=synonym.concept_id, concept_ref=f"{level}:{synonym.concept_id}",
        concept_name=synonym.concept_name, action=action,
        before=before.model_dump(mode="json") if before else None, after=after.model_dump(mode="json"),
        reason=after.removal_reason if action == "synonym_remove" else after.reason,
        actor=after.removed_by if action == "synonym_remove" else after.actor,
        occurred_at=after.removed_at if action == "synonym_remove" else after.created_at,
        scope="live",
    )


def _time(value: Any) -> str | None:
    return value.isoformat(timespec="microseconds") if value is not None else None


def _row(synonym: Synonym) -> tuple[Any, ...]:
    data = synonym.model_dump()
    return tuple(_time(data[c]) if c in ("created_at", "removed_at") else data[c] for c in COLUMNS)


def _synonym(row: tuple[Any, ...]) -> Synonym:
    return Synonym.model_validate(dict(zip(COLUMNS, row, strict=True)))
