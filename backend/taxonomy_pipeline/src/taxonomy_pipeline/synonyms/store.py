"""SQLite storage for synonyms, in the same pipeline-owned database as run history and the audit log.

Each add or remove commits together with its audit event (`synonym_add` /
`synonym_remove`), so the table and its history can't disagree. Rows are never
deleted: removing a synonym marks it `removed`, which keeps who added it and why.
A partial unique index allows one approved mapping per normalised term, concept and
scope; a removed mapping can be added again as a new row. The caller's conflict
check (`SynonymIndex.check_new`) runs inside the write transaction against the
approved synonyms read under the writer lock, so two concurrent adds can't both pass.

Nothing here writes to taxonomy files or the client's database. The schema is
versioned in `taxonomy_synonym_schema` (as the audit log versions its own):
version 2 added the scope columns (#61), and a version 1 table is migrated in place.
A global synonym stores its scope as empty strings, because SQLite treats NULLs as
distinct in a unique index and would let duplicate global mappings through.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
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

SCHEMA_VERSION = 2
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS taxonomy_synonyms (
        synonym_id TEXT PRIMARY KEY,
        scope_level TEXT NOT NULL DEFAULT '',
        scope_id TEXT NOT NULL DEFAULT '',
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
    "CREATE INDEX IF NOT EXISTS synonym_term ON taxonomy_synonyms(dataset, normalized_term)",
)
UNIQUE_MAPPING = """CREATE UNIQUE INDEX IF NOT EXISTS synonym_approved_mapping
    ON taxonomy_synonyms(dataset, concept_level, concept_id, normalized_term, scope_level, scope_id)
    WHERE status = 'approved'"""
VERSION_TABLE = """CREATE TABLE IF NOT EXISTS taxonomy_synonym_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL
)"""
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
    request: SynonymCreate, *, canonical_name: str, snapshot_id: str,
    check: Callable[[list[Synonym]], None] | None = None, db_path: Path | str | None = None,
) -> Synonym:
    """Store one approved synonym and its `synonym_add` audit event in one transaction.

    `canonical_name` and `snapshot_id` come from the taxonomy the caller checked the
    concept against (`concept_name`). A term equal to that name, or one that is
    already an approved synonym of the concept in the same scope, is refused. `check`
    gets the dataset's approved synonyms, read under the writer lock, and raises to refuse.
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
        actor=request.actor, created_at=utc_now(), scope_level=request.scope_level, scope_id=request.scope_id,
    )
    with _writer(db_path) as connection:
        existing = connection.execute(
            "SELECT synonym_id FROM taxonomy_synonyms WHERE status = 'approved' AND dataset = ? "
            "AND concept_level = ? AND concept_id = ? AND normalized_term = ? AND scope_level = ? AND scope_id = ?",
            (synonym.dataset, synonym.concept_level, synonym.concept_id, normalized,
             synonym.scope_level or "", synonym.scope_id or ""),
        ).fetchone()
        if existing:
            raise DuplicateSynonym(
                f"{request.term!r} is already an approved synonym of {canonical_name!r} (synonym {existing[0]})"
            )
        if check is not None:
            check(_approved(connection, synonym.dataset))
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
    """Create or migrate the tables inside the caller's write transaction; refuse an unknown version."""
    connection.execute(VERSION_TABLE)
    row = connection.execute("SELECT version FROM taxonomy_synonym_schema WHERE singleton = 1").fetchone()
    version = row[0] if row else None
    if version == SCHEMA_VERSION:
        return
    if version is None:
        for statement in (*SCHEMA, UNIQUE_MAPPING):
            connection.execute(statement)
        connection.execute("INSERT INTO taxonomy_synonym_schema (singleton, version) VALUES (1, ?)", (SCHEMA_VERSION,))
    elif version == 1:  # #59's table: add the scope columns and widen the unique mapping to include them
        connection.execute("ALTER TABLE taxonomy_synonyms ADD COLUMN scope_level TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE taxonomy_synonyms ADD COLUMN scope_id TEXT NOT NULL DEFAULT ''")
        connection.execute("DROP INDEX IF EXISTS synonym_approved_mapping")
        connection.execute(UNIQUE_MAPPING)
        connection.execute("UPDATE taxonomy_synonym_schema SET version = ? WHERE singleton = 1", (SCHEMA_VERSION,))
    else:
        raise SynonymError(f"Unsupported synonym schema version {version}; expected {SCHEMA_VERSION}")


def _approved(connection: sqlite3.Connection, dataset: str) -> list[Synonym]:
    rows = connection.execute(
        f"SELECT {', '.join(COLUMNS)} FROM taxonomy_synonyms WHERE status = 'approved' AND dataset = ?", (dataset,),
    )
    return [_synonym(row) for row in rows]


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
    data["scope_level"], data["scope_id"] = data["scope_level"] or "", data["scope_id"] or ""
    return tuple(_time(data[c]) if c in ("created_at", "removed_at") else data[c] for c in COLUMNS)


def _synonym(row: tuple[Any, ...]) -> Synonym:
    data = dict(zip(COLUMNS, row, strict=True))
    data["scope_level"], data["scope_id"] = data["scope_level"] or None, data["scope_id"] or None
    return Synonym.model_validate(data)
