"""SQLite-backed run history, so a future dashboard has something to list,
inspect, and diff. Deliberately simple: one table, the full report stored as
JSON - swap for a real database only if/when run volume actually calls for
it (see the project's "don't add infra before it's needed" call).

The independent audit context owns its append-only table in the same database;
this module's merge/update behavior is only for run records, never audit events.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlmodel import Field, Session, SQLModel, col, create_engine, select

from .models import RunReport

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "taxonomy_pipeline.sqlite3"

_engines: dict[str, Any] = {}


class RunRecord(SQLModel, table=True):
    run_id: str = Field(primary_key=True)
    status: str
    created_at: str
    finished_at: str | None = None
    config_json: str
    report_json: str
    error: str | None = None


def get_engine(db_path: Path | str | None = None):
    # Resolved from the module global at call time (not baked into a default
    # argument) so tests can monkeypatch DEFAULT_DB_PATH per-test.
    db_path = db_path or DEFAULT_DB_PATH
    key = str(db_path)
    if key not in _engines:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)
        _engines[key] = engine
    return _engines[key]


def save_run(report: RunReport, config_json: str, db_path: Path | str | None = None) -> None:
    engine = get_engine(db_path)
    record = RunRecord(
        run_id=report.run_id,
        status=report.status,
        created_at=report.created_at.isoformat(),
        finished_at=report.finished_at.isoformat() if report.finished_at else None,
        config_json=config_json,
        report_json=report.model_dump_json(),
        error=report.error,
    )
    with Session(engine) as session:
        session.merge(record)
        session.commit()


def get_run(run_id: str, db_path: Path | str | None = None) -> RunRecord | None:
    engine = get_engine(db_path)
    with Session(engine) as session:
        return session.get(RunRecord, run_id)


def list_runs(db_path: Path | str | None = None, limit: int = 50) -> list[RunRecord]:
    engine = get_engine(db_path)
    with Session(engine) as session:
        statement = select(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit)
        return list(session.exec(statement))


class RunHistoryRow(SQLModel):
    """A run record without `report_json`, for listings that never read the report."""

    run_id: str
    status: str
    created_at: str
    config_json: str


def list_run_history(db_path: Path | str | None = None, limit: int = 50) -> list[RunHistoryRow]:
    """The newest runs like `list_runs`, leaving out each report: a judged saturation report runs to megabytes."""
    engine = get_engine(db_path)
    columns = (col(RunRecord.run_id), col(RunRecord.status), col(RunRecord.created_at), col(RunRecord.config_json))
    with Session(engine) as session:
        statement = select(*columns).order_by(col(RunRecord.created_at).desc()).limit(limit)
        return [
            RunHistoryRow(run_id=run_id, status=status, created_at=created_at, config_json=config_json)
            for run_id, status, created_at, config_json in session.exec(statement)
        ]
