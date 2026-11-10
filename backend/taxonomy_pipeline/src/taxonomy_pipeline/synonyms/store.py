"""SQLite storage for synonyms, in the same pipeline-owned database as run history and the audit log.

Each add or remove commits together with its audit event (`synonym_add` /
`synonym_remove`), so the table and its history can't disagree. Rows are never
deleted: removing a synonym marks it `removed`, which keeps who added it and why.

Terms are compared by `match_key` (stored as `term_key`), the fold `matching/names.py`
uses for "the same concept?": "Audits" is the same term as "audit", and "Data and
Analytics" the same as "Data & Analytics". A partial unique index allows one approved
mapping per term key, concept and scope; a removed mapping can be added again as a new
row. The caller's conflict check (`SynonymIndex.check_new`) runs inside the write
transaction against the approved synonyms with the same term key, read under the writer
lock, so two concurrent adds can't both pass; the taxonomy side of that check is built
before the lock is taken.

A synonym belongs to a dataset *name* (`original`), not one snapshot, so it survives
enrichment: `integrate` keeps the baseline's IDs. The guard is the concept's name: a
synonym records the name its concept had, and adding to an ID whose synonyms were
recorded under a different name is refused, since that dataset name now points at a
different taxonomy. Readers apply the same check (`SynonymIndex`, the tag lookup).

Nothing here writes to taxonomy files or the client's database. The schema is
versioned in `taxonomy_synonym_schema` (as the audit log versions its own): version 2
added the scope columns (#61), and a version 1 table is migrated in place on the next
write. A global synonym stores its scope as empty strings, because SQLite treats NULLs
as distinct in a unique index and would let duplicate global mappings through. Every
write then reruns the idempotent `CREATE ... IF NOT EXISTS` statements, so a dropped
table or index is recreated, and readers select only the columns a table has, so an
older table reads (missing scope = global) before anything has migrated it.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .. import db
from ..audit import ChangeEvent, ensure_schema, log_change
from ..audit.models import utc_now
from ..matching.names import is_valid_name, match_key, normalize
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
        term_key TEXT NOT NULL,
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
        ON taxonomy_synonyms(dataset, concept_level, concept_id, term_key, scope_level, scope_id)
        WHERE status = 'approved'""",
    "CREATE INDEX IF NOT EXISTS synonym_term ON taxonomy_synonyms(dataset, term_key)",
)
VERSION_TABLE = """CREATE TABLE IF NOT EXISTS taxonomy_synonym_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL
)"""
COLUMNS = tuple(Synonym.model_fields)
# Columns a table can lack (it predates them), with what such a row means.
DEFAULTS = {"scope_level": "''", "scope_id": "''"}


class SynonymError(ValueError):
    """A synonym request that can't be applied; the message says why."""


class UnknownConcept(SynonymError):
    """The dataset or concept a synonym would point at doesn't exist where it was looked for."""


class TaxonomyUnavailable(SynonymError):
    """The taxonomy to check against couldn't be loaded: a broken config, or a dataset file that is gone."""


class RetiredConcept(SynonymError):
    """The concept exists but is retired (status 0), so new terms shouldn't resolve to it."""


class DuplicateSynonym(SynonymError):
    """The same term is already an approved synonym of that concept."""


class SynonymNotFound(SynonymError):
    """No synonym in the expected status has that ID."""


class UnsupportedSchema(SynonymError):
    """The synonym table was written by another version of this code."""


def concept_name(taxonomy: TaxonomyDataset, level: SynonymLevel, concept_id: str) -> str:
    """The canonical name of one active concept. IDs compare as strings, as the audit log stores them, and a
    repeated ID resolves to its last row, as `Hierarchy` does. Raises UnknownConcept or RetiredConcept."""
    row = next((row for row in reversed(getattr(taxonomy, level)) if str(row.get("id")) == concept_id), None)
    if row is None or not str(row.get("name") or "").strip():
        raise UnknownConcept(f"{taxonomy.name} has no {AUDIT_LEVELS[level]} with ID {concept_id}")
    if str(row.get("status")) == "0":
        raise RetiredConcept(f"{AUDIT_LEVELS[level]} {concept_id} ({row['name']!r}) is retired (status 0)")
    return str(row["name"])


def add_synonym(
    request: SynonymCreate, *, canonical_name: str, snapshot_id: str,
    check: Callable[[list[Synonym]], None] | None = None, db_path: Path | str | None = None,
) -> Synonym:
    """Store one approved synonym and its `synonym_add` audit event in one transaction.

    `canonical_name` and `snapshot_id` come from the taxonomy the caller checked the
    concept against (`concept_name`). Refused: a term with no letters or digits or a
    placeholder ("test", "new"); the concept's own name or a spelling variant of it;
    a term already approved for the concept in the same scope; and a concept whose
    existing synonyms were recorded under a different name. `check` gets the dataset's
    approved synonyms with the same term key, read under the writer lock, and raises
    to refuse.
    """
    if not is_valid_name(request.term):
        raise SynonymError(f"synonym {request.term!r} has no letters or digits, or is a placeholder")
    key = match_key(request.term)
    if key == match_key(canonical_name):
        raise SynonymError(f"{request.term!r} is the concept's own name ({canonical_name!r}) or a spelling of it")
    synonym = Synonym(
        synonym_id=str(uuid4()), term=request.term, normalized_term=normalize(request.term), term_key=key,
        dataset=request.dataset, concept_level=request.concept_level, concept_id=request.concept_id,
        concept_name=canonical_name, snapshot_id=snapshot_id, status="approved", source=request.source,
        reason=request.reason, actor=request.actor, created_at=utc_now(), scope_level=request.scope_level,
        scope_id=request.scope_id,
    )
    with _writer(db_path) as connection:
        for existing in _rows(connection, "status = 'approved' AND dataset = ? AND concept_level = ? "
                              "AND concept_id = ?", (synonym.dataset, synonym.concept_level, synonym.concept_id)):
            if match_key(existing.concept_name) != match_key(canonical_name):
                raise SynonymError(
                    f"{synonym.dataset} {AUDIT_LEVELS[synonym.concept_level]} {synonym.concept_id} has synonyms "
                    f"recorded for {existing.concept_name!r}, but is {canonical_name!r} here: this dataset name "
                    "now points at a different taxonomy; give it its own name in the config"
                )
            if existing.term_key == key and existing.scope == synonym.scope:
                raise DuplicateSynonym(f"{request.term!r} is already an approved synonym of {canonical_name!r} "
                                       f"(synonym {existing.synonym_id})")
        if check is not None:
            check(approved_with_key(connection, synonym.dataset, key))
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
        found = _rows(connection, "synonym_id = ? AND status = 'approved'", (synonym_id,))
        if not found:
            raise SynonymNotFound(f"no approved synonym with ID {synonym_id!r}")
        before = found[0]
        try:
            after = Synonym.model_validate({
                **before.model_dump(), "status": "removed", "removed_at": utc_now(), "removed_by": actor,
                "removal_reason": reason,
            })
        except ValidationError as exc:
            raise SynonymError("removing a synonym needs a non-blank reason and actor") from exc
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
    clauses, params = [], []
    for field in ("dataset", "concept_level", "concept_id"):
        value = getattr(query, field)
        if value is not None:
            clauses.append(f"{field} = ?")
            params.append(value)
    if query.term is not None:
        clauses.append("term_key = ?")
        params.append(match_key(query.term))
    if not query.include_removed:
        clauses.append("status = 'approved'")
    where = " AND ".join(clauses) or "1"
    with _reader(db_path) as connection:
        if connection is None:
            return result
        result.total = connection.execute(f"SELECT COUNT(*) FROM taxonomy_synonyms WHERE {where}", params).fetchone()[0]
        result.synonyms = _rows(connection, f"{where} ORDER BY created_at, synonym_id LIMIT ? OFFSET ?",
                                (*params, query.limit, query.offset))
    return result


def approved_synonyms(
    dataset: str, *, term: str | None = None, db_path: Path | str | None = None,
) -> list[Synonym]:
    """The approved synonyms of one dataset (only those whose term key is `term`'s, if given), oldest first,
    read without creating the database. With `term` the read uses the `synonym_term` index."""
    where, params = "status = 'approved' AND dataset = ?", (dataset,)
    if term is not None:
        where, params = where + " AND term_key = ?", (*params, match_key(term))
    with _reader(db_path) as connection:
        if connection is None:
            return []
        return _rows(connection, f"{where} ORDER BY created_at, synonym_id", params)


def approved_with_key(connection: sqlite3.Connection, dataset: str, key: str) -> list[Synonym]:
    """The dataset's approved synonyms whose term key is `key`: all a conflict check for that term reads."""
    return _rows(connection, "status = 'approved' AND dataset = ? AND term_key = ?", (dataset, key))


@contextmanager
def _writer(db_path: Path | str | None) -> Iterator[sqlite3.Connection]:
    """One `BEGIN IMMEDIATE` transaction with both schemas in place: commits on success, rolls back on error."""
    path = Path(db_path or db.DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        ensure_schema(connection)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            _ensure_schema(connection)
            yield connection


@contextmanager
def _reader(db_path: Path | str | None) -> Iterator[sqlite3.Connection | None]:
    """A read-only connection, or None when there is no database or synonym table yet (never creates either)."""
    path = Path(db_path or db.DEFAULT_DB_PATH).resolve()
    if not path.exists():
        yield None
        return
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        found = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='taxonomy_synonyms'",
        ).fetchone()
        yield connection if found else None


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Migrate an older table, refuse an unknown version, then (re)create every table and index idempotently."""
    connection.execute(VERSION_TABLE)
    row = connection.execute("SELECT version FROM taxonomy_synonym_schema WHERE singleton = 1").fetchone()
    version = row[0] if row else None
    if version == 1:  # #59's table: add the scope columns; the unique mapping is recreated with them below
        connection.execute("ALTER TABLE taxonomy_synonyms ADD COLUMN scope_level TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE taxonomy_synonyms ADD COLUMN scope_id TEXT NOT NULL DEFAULT ''")
        connection.execute("DROP INDEX IF EXISTS synonym_approved_mapping")
    elif version not in (None, SCHEMA_VERSION):
        raise UnsupportedSchema(f"Unsupported synonym schema version {version}; expected {SCHEMA_VERSION}")
    for statement in SCHEMA:
        connection.execute(statement)
    if version is None:
        connection.execute("INSERT INTO taxonomy_synonym_schema (singleton, version) VALUES (1, ?)", (SCHEMA_VERSION,))
    elif version != SCHEMA_VERSION:
        connection.execute("UPDATE taxonomy_synonym_schema SET version = ? WHERE singleton = 1", (SCHEMA_VERSION,))


def _rows(connection: sqlite3.Connection, where: str, params: tuple[Any, ...] = ()) -> list[Synonym]:
    """Synonyms matching `where`, selecting only the columns this table has (older ones lack some)."""
    present = {row[1] for row in connection.execute("PRAGMA table_info(taxonomy_synonyms)")}
    columns = ", ".join(
        column if column in present else f"{DEFAULTS.get(column, 'NULL')} AS {column}" for column in COLUMNS
    )
    rows = connection.execute(f"SELECT {columns} FROM taxonomy_synonyms WHERE {where}", params)
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
