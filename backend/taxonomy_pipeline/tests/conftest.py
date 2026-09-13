from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point taxonomy_pipeline.db at a throwaway SQLite file for this test."""
    from taxonomy_pipeline import db

    db_path = tmp_path / "test-runs.sqlite3"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    return db_path
