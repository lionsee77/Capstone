from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def classification_paths(tmp_path):
    from openpyxl import Workbook

    definitions = {
        "ssic": ("2025", [("A", "Agriculture"), ("01", "Accounting")]),
        "ssoc": ("2024", [("2", "Professionals"), ("24", "Business professionals"),
                           ("241", "Accountants")]),
    }
    paths = {}
    for kind, (version, rows) in definitions.items():
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = f"{kind.upper()} {version} Structure"
        sheet.append([f"{kind.upper()} {version}", f"{kind.upper()} {version} Title"])
        for row in rows:
            sheet.append(row)
        path = tmp_path / f"{kind}.xlsx"
        workbook.save(path)
        workbook.close()
        paths[kind] = path
    return paths


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point taxonomy_pipeline.db at a throwaway SQLite file for this test."""
    from taxonomy_pipeline import db

    db_path = tmp_path / "test-runs.sqlite3"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    return db_path
