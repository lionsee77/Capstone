"""`/synonyms` adds, lists and removes synonyms against a past run's dataset, behind the API token."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxonomy_pipeline import db
from taxonomy_pipeline.api.app import app
from taxonomy_pipeline.app.scoring import run_pipeline
from taxonomy_pipeline.config import PipelineConfig
from tests.conftest import FIXTURES


@pytest.fixture
def run_id(isolated_db: Path) -> str:
    config = PipelineConfig.model_validate({
        "datasets": [{"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]}],
        "metrics": [{"id": "integrity", "params": {"dataset": "original"}}],
    })
    report = run_pipeline(config)
    db.save_run(report, config_json=config.model_dump_json())
    return report.run_id


def _body(run_id: str, **changes: str) -> dict:
    return {"run_id": run_id, "dataset": "original", "concept_level": "tags", "concept_id": "101",
            "term": "Goods and Services Tax", "reason": "Expansion", "actor": "reviewer", **changes}


def test_adds_lists_and_removes_a_synonym(run_id: str) -> None:
    with TestClient(app) as client:
        created = client.post("/synonyms", json=_body(run_id))
        assert created.status_code == 201
        synonym = created.json()
        assert (synonym["concept_name"], synonym["status"]) == ("gst", "approved")

        listed = client.get("/synonyms", params={"concept_level": "tags", "concept_id": "101"}).json()
        assert [s["synonym_id"] for s in listed["synonyms"]] == [synonym["synonym_id"]]

        removed = client.post(f"/synonyms/{synonym['synonym_id']}/remove", json={"reason": "Unused", "actor": "lead"})
        assert removed.status_code == 200 and removed.json()["status"] == "removed"
        assert client.get("/synonyms").json()["total"] == 0
        assert client.get("/synonyms", params={"include_removed": True}).json()["total"] == 1


@pytest.mark.parametrize(("changes", "status"), [
    ({"concept_id": "999"}, 404),
    ({"dataset": "missing"}, 404),
    ({"term": "gst"}, 422),
    ({"term": "   "}, 422),
    ({"concept_level": "services"}, 422),
])
def test_refused_requests_return_client_errors(run_id: str, changes: dict, status: int) -> None:
    with TestClient(app) as client:
        assert client.post("/synonyms", json=_body(run_id, **changes)).status_code == status
        assert client.get("/synonyms").json()["total"] == 0


def test_unknown_run_is_404_and_duplicate_is_409(run_id: str) -> None:
    with TestClient(app) as client:
        assert client.post("/synonyms", json=_body("no-such-run")).status_code == 404
        assert client.post("/synonyms", json=_body(run_id)).status_code == 201
        assert client.post("/synonyms", json=_body(run_id, term="goods and services tax.")).status_code == 409


def test_removing_an_unknown_synonym_is_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        assert client.post("/synonyms/missing/remove", json={"reason": "Gone"}).status_code == 404


def test_synonym_routes_require_the_api_token(monkeypatch: pytest.MonkeyPatch, isolated_db: Path) -> None:
    monkeypatch.delenv("TAXONOMY_API_AUTH")
    monkeypatch.setenv("TAXONOMY_API_TOKEN", "test-secret")
    with TestClient(app) as client:
        assert client.get("/synonyms").status_code == 401
        assert client.post("/synonyms", json={}).status_code == 401
        assert client.get("/synonyms", headers={"Authorization": "Bearer test-secret"}).status_code == 200


def test_a_runs_dataset_whose_file_has_moved_is_404_not_500(run_id: str, tmp_path: Path) -> None:
    from taxonomy_pipeline.config import PipelineConfig

    moved = tmp_path / "moved.sql"
    moved.write_text((FIXTURES / "mini_original.sql").read_text(encoding="utf-8"), encoding="utf-8")
    config = PipelineConfig.model_validate({
        "datasets": [{"name": "original", "paths": [str(moved)]}],
        "metrics": [{"id": "integrity", "params": {"dataset": "original"}}],
    })
    report = run_pipeline(config)
    db.save_run(report, config_json=config.model_dump_json())
    moved.unlink()
    with TestClient(app) as client:
        response = client.post("/synonyms", json=_body(report.run_id))
    assert response.status_code == 404 and "couldn't be loaded" in response.json()["detail"]


def test_remove_and_list_map_store_errors_instead_of_500(run_id: str, isolated_db: Path) -> None:
    import sqlite3
    from contextlib import closing

    with TestClient(app) as client:
        assert client.post("/synonyms/%20/remove", json={"reason": "x"}).status_code == 404
        assert client.post("/synonyms", json=_body(run_id)).status_code == 201
        with closing(sqlite3.connect(isolated_db)) as connection, connection:
            connection.execute("UPDATE taxonomy_synonym_schema SET version = 99")
        assert client.post("/synonyms", json=_body(run_id, term="GST levy")).status_code == 503
        assert client.post("/synonyms/x/remove", json={"reason": "x"}).status_code == 503
