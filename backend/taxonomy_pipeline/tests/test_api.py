from __future__ import annotations

import time

from fastapi.testclient import TestClient

from taxonomy_pipeline.api.app import app
from taxonomy_pipeline.quality.definitions import METRIC_DEFINITIONS_VERSION
from tests.conftest import FIXTURES


def _config_payload() -> dict:
    return {
        "datasets": [
            {"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]},
        ],
        "metrics": [
            {"id": "integrity", "params": {"dataset": "original"}},
        ],
    }


def test_list_metrics_is_self_describing():
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body.keys() == {"integrity", "depth", "breadth", "saturation"}
    assert "description" in body["integrity"]


def test_run_and_metric_routes_declare_their_response_models():
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    def response_schema(path: str, method: str, status: str = "200") -> dict:
        return paths[path][method]["responses"][status]["content"]["application/json"]["schema"]

    assert response_schema("/metrics", "get")["additionalProperties"]["$ref"].endswith("/MetricInfo")
    assert response_schema("/runs", "post", "202")["$ref"].endswith("/RunCreated")
    assert response_schema("/runs", "get")["items"]["$ref"].endswith("/RunSummary")


def test_run_list_rows_keep_the_stored_timestamp(isolated_db):
    client = TestClient(app)
    created = client.post("/runs", json=_config_payload()).json()
    assert created["status"] == "queued"

    rows = client.get("/runs").json()

    assert rows[0].keys() == {"run_id", "status", "created_at"}
    assert rows[0]["run_id"] == created["run_id"] and "T" in rows[0]["created_at"]


def test_case_as_placement_step_is_not_listed_as_a_metric():
    # It places tags rather than scoring a taxonomy; case-a calls it directly.
    assert "category_adaptation" not in TestClient(app).get("/metrics").json()


def test_create_and_poll_a_run(isolated_db):
    client = TestClient(app)

    created = client.post("/runs", json=_config_payload())
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    # BackgroundTasks may finish before or shortly after the response returns
    # depending on the ASGI transport - poll briefly either way.
    report = None
    for _ in range(20):
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        report = response.json()
        if report["status"] in ("done", "failed"):
            break
        time.sleep(0.05)

    assert report["status"] == "done", report
    assert report["results"][0]["metric_id"] == "integrity"


def test_a_run_report_says_which_metric_definitions_scored_it(isolated_db):
    client = TestClient(app)

    # TestClient runs BackgroundTasks before returning, so each run has finished by now.
    current = client.post("/runs", json=_config_payload()).json()["run_id"]
    pinned_elsewhere = client.post(
        "/runs", json={**_config_payload(), "metric_definitions_version": METRIC_DEFINITIONS_VERSION + 1},
    ).json()["run_id"]

    assert client.get(f"/runs/{current}").json()["metric_definitions_version"] == METRIC_DEFINITIONS_VERSION
    failed = client.get(f"/runs/{pinned_elsewhere}").json()
    assert failed["status"] == "failed" and "config pins metric definitions" in failed["error"]


def test_create_run_rejects_relative_dataset_paths(isolated_db):
    payload = _config_payload()
    payload["datasets"][0]["paths"] = ["../../sql-files-nus/categories.sql"]

    response = TestClient(app).post("/runs", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "API dataset paths must be absolute server-side paths."
    assert response.json()["detail"]["relative_paths"] == [
        {"dataset": "original", "path": "../../sql-files-nus/categories.sql"}
    ]


def test_get_unknown_run_is_404(isolated_db):
    client = TestClient(app)
    response = client.get("/runs/does-not-exist")
    assert response.status_code == 404


def test_run_is_marked_running_while_the_pipeline_executes(isolated_db, monkeypatch):
    from taxonomy_pipeline import db
    from taxonomy_pipeline.app import scoring
    from taxonomy_pipeline.models import RunReport

    statuses_seen_mid_run = []

    def fake_run_pipeline(config):
        # The only row in the isolated DB is this run; run_id isn't bound yet
        # because the background task executes before client.post returns.
        statuses_seen_mid_run.extend(record.status for record in db.list_runs())
        return RunReport(status="done")

    monkeypatch.setattr(scoring, "run_pipeline", fake_run_pipeline)
    client = TestClient(app)

    created = client.post("/runs", json=_config_payload())
    run_id = created.json()["run_id"]
    # TestClient runs BackgroundTasks before returning, so by now it's finished.
    assert statuses_seen_mid_run == ["running"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "done"
