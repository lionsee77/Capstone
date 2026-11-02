"""The audit history endpoint is authenticated, filterable and read-only."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxonomy_pipeline.api.app import app
from taxonomy_pipeline.audit import ChangeEvent, log_change


# Looking up a proposal's history changes neither taxonomy artifacts nor the audit database.
def test_history_query_preserves_taxonomy_and_database(
    audit_event: ChangeEvent, isolated_db: Path, saved_proposal_run: Path,
) -> None:
    log_change(audit_event)
    files = [*saved_proposal_run.iterdir(), saved_proposal_run.parents[1] / "baseline.json", isolated_db]
    before = {path: path.read_bytes() for path in files if path.is_file()}
    with TestClient(app) as client:
        response = client.get("/changes", params={"proposal_id": "p1", "concept_id": "1", "action": "add"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["changes"][0]["event_id"] == audit_event.event_id
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"action": "invalid"}, {"since": "2026-10-30T00:00:00"},
    {"since": "2026-10-31T00:00:00Z", "until": "2026-10-30T00:00:00Z"},
])
# Validation errors surface as client errors rather than server exceptions or ignored filters.
def test_invalid_history_filters_return_422(params: dict) -> None:
    with TestClient(app) as client:
        assert client.get("/changes", params=params).status_code == 422


# The shared token protects the new route and no mutation routes are exposed.
def test_history_requires_authentication_and_has_no_write_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXONOMY_API_AUTH")
    monkeypatch.setenv("TAXONOMY_API_TOKEN", "test-secret")
    with TestClient(app) as client:
        assert client.get("/changes").status_code == 401
        assert client.get("/changes", headers={"Authorization": "Bearer test-secret"}).status_code == 200
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert client.request(method, "/changes").status_code == 405
