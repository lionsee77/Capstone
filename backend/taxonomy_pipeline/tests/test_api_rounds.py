"""`/rounds` as a thin adapter: its roots, statuses and upload limits."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from taxonomy_pipeline.api.app import app
from taxonomy_pipeline.app import rounds
from tests.conftest import FIXTURES

CSV = b"Sector,Role,Skill\nAccounting,Tax Advisory,Underwater Welding\n"
MAPPING = {
    "name": "demo", "display_name": "Demo", "source": "flat.csv", "service_id": 1,
    "columns": {"category": "Sector", "speciality": "Role", "tag": "Skill"},
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_db: Path) -> TestClient:
    snapshot = tmp_path / "integrations" / "base"
    (snapshot / "baseline").mkdir(parents=True)
    # The real taxonomy's service names are the only ones the classification contract accepts.
    baseline = (FIXTURES / "mini_original.sql").read_text("utf-8").replace("Professional Services", "Industry")
    (snapshot / "baseline" / "original.sql").write_text(baseline, "utf-8")
    (snapshot / "additions.sql").write_text("", "utf-8")
    (snapshot / "enriched.yaml").write_text(yaml.safe_dump({
        "datasets": [{"name": "enriched", "type": "sql_dump", "paths": ["baseline/original.sql", "additions.sql"]}],
        "metrics": [],
    }), "utf-8")
    (snapshot / "manifest.json").write_text(json.dumps({
        "created_at": "2026-10-01T00:00:00+00:00", "baseline": {"files": ["baseline/original.sql"]},
    }), "utf-8")
    template = tmp_path / "template.yaml"
    template.write_text(yaml.safe_dump({
        "datasets": [{"name": "original", "paths": ["o.sql"]}, {"name": "incoming", "paths": ["i.sql"]}],
        "metrics": [{"id": "saturation", "params": {
            "baseline": "original", "incoming": ["incoming"], "threshold": 90,
        }}],
    }), "utf-8")
    monkeypatch.setenv("TAXONOMY_ROUNDS_ROOT", str(tmp_path / "rounds"))
    monkeypatch.setenv("TAXONOMY_INTEGRATIONS_ROOT", str(tmp_path / "integrations"))
    monkeypatch.setenv("TAXONOMY_MERGE_ROOT", str(tmp_path / "merge_runs"))
    monkeypatch.setattr(rounds, "SEMANTIC_MODEL", "")
    monkeypatch.setattr(rounds, "DEFAULT_TEMPLATE", template)
    return TestClient(app)


def _created(client: TestClient, round_id: str = "r1") -> TestClient:
    response = client.post("/rounds", json={"round_id": round_id, "baseline_snapshot": "base"})
    assert response.status_code == 201
    return client


def test_a_round_is_created_then_listed_and_read(client: TestClient) -> None:
    _created(client)

    assert [state["round_id"] for state in client.get("/rounds").json()] == ["r1"]
    body = client.get("/rounds/r1").json()
    assert body["status"] == "setup"
    assert [stage["id"] for stage in body["stages"]][0] == "import"


def test_options_list_snapshots_and_say_llm_is_off_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAXONOMY_ALLOW_LLM", raising=False)

    body = client.get("/rounds/options").json()

    assert body["snapshots"] == ["base"]
    assert body["llm_allowed"] is False


def test_uploading_stores_the_raw_bytes_and_reports_the_columns(client: TestClient) -> None:
    _created(client)

    response = client.put("/rounds/r1/files/flat.csv", content=CSV)

    assert response.status_code == 201
    assert response.json()["columns"] == ["Sector", "Role", "Skill"]
    assert client.get("/rounds/r1/files").json()[0]["name"] == "flat.csv"
    assert client.get("/rounds/r1/files/flat.csv").json()["row_count"] == 1


def test_saving_and_reading_an_upload_stays_off_the_event_loop(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _created(client)
    real = rounds.add_file
    loops = []

    def recording(*args: object) -> object:
        try:
            loops.append(asyncio.get_running_loop())
        except RuntimeError:
            loops.append(None)
        return real(*args)

    monkeypatch.setattr(rounds, "add_file", recording)

    assert client.put("/rounds/r1/files/flat.csv", content=CSV).status_code == 201
    assert loops == [None]


def test_an_upload_past_the_limit_is_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _created(client)
    monkeypatch.setattr(rounds, "MAX_UPLOAD_BYTES", 4)

    assert client.put("/rounds/r1/files/flat.csv", content=CSV).status_code == 413


def test_a_bad_file_name_is_422_and_cannot_escape_the_round(client: TestClient, tmp_path: Path) -> None:
    _created(client)

    assert client.put("/rounds/r1/files/notes.txt", content=b"x").status_code == 422
    assert client.put("/rounds/r1/files/..%2F..%2Fevil.csv", content=b"x").status_code in {404, 422}
    assert not list(tmp_path.rglob("evil.csv"))


def test_configuring_and_running_reach_review_in_the_background(client: TestClient) -> None:
    _created(client)
    client.put("/rounds/r1/files/flat.csv", content=CSV)

    configured = client.put("/rounds/r1/config", json={"mapping": MAPPING})
    assert configured.status_code == 200
    assert configured.json()["status"] == "ready"
    assert "round_baseline" in client.get("/rounds/r1/config").json()["config"]

    started = client.post("/rounds/r1/run")

    assert started.status_code == 202
    body = client.get("/rounds/r1").json()  # TestClient runs background tasks before returning
    assert body["status"] in {"review", "ready_to_integrate"}
    assert client.post("/rounds/r1/run").status_code == 409


def test_a_bad_mapping_is_422_with_the_reason(client: TestClient) -> None:
    _created(client)
    client.put("/rounds/r1/files/flat.csv", content=CSV)

    response = client.put("/rounds/r1/config", json={"mapping": {**MAPPING, "columns": {
        "category": "Sector", "speciality": "Role", "tag": "Nope",
    }}})

    assert response.status_code == 422
    assert "Nope" in response.json()["detail"]


def test_settings_turning_on_the_judge_are_refused_when_the_server_forbids_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAXONOMY_ALLOW_LLM", raising=False)
    _created(client)
    client.put("/rounds/r1/files/flat.csv", content=CSV)

    response = client.put("/rounds/r1/config", json={"mapping": MAPPING, "settings": {"judge": True}})

    assert response.status_code == 422
    assert "TAXONOMY_ALLOW_LLM" in response.json()["detail"]


def test_a_caller_cannot_slip_a_judge_cache_path_into_the_settings(client: TestClient) -> None:
    _created(client)
    client.put("/rounds/r1/files/flat.csv", content=CSV)

    response = client.put("/rounds/r1/config", json={"mapping": MAPPING, "settings": {"cache": "/tmp/x.json"}})

    assert response.status_code == 422


def test_post_runs_still_refuses_judge_settings(client: TestClient) -> None:
    config = {"datasets": [{"name": "a", "paths": ["/x.sql"]}],
              "metrics": [{"id": "saturation", "params": {"judge": {"model": "m"}}}]}

    assert client.post("/runs", json=config).status_code == 422


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/rounds/missing"), ("delete", "/rounds/missing"), ("post", "/rounds/missing/run"),
    ("get", "/rounds/missing/files"), ("get", "/rounds/missing/config"),
])
def test_an_unknown_round_is_404(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 404


def test_a_round_is_removed_with_delete(client: TestClient) -> None:
    _created(client)

    assert client.delete("/rounds/r1").status_code == 204
    assert client.get("/rounds/r1").status_code == 404


def test_a_duplicate_round_name_is_409(client: TestClient) -> None:
    _created(client)

    response = client.post("/rounds", json={"round_id": "r1", "baseline_snapshot": "base"})

    assert response.status_code == 409
