from __future__ import annotations

import time

from fastapi.testclient import TestClient

from taxonomy_pipeline.api.app import app
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
    assert {"integrity", "depth", "breadth", "saturation"} <= body.keys()
    assert "description" in body["integrity"]


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


def test_get_unknown_run_is_404(isolated_db):
    client = TestClient(app)
    response = client.get("/runs/does-not-exist")
    assert response.status_code == 404
