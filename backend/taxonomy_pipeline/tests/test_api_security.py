"""Test the API's access control: the bearer token and the dataset-path allowlist."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxonomy_pipeline import db
from taxonomy_pipeline.api.app import app
from taxonomy_pipeline.api.security import DEFAULT_DATA_ROOT, PLACEHOLDER_TOKENS, data_roots

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "test-token"


@pytest.fixture
def secured(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The app with auth on (the conftest default turns it off) and a token configured."""
    monkeypatch.delenv("TAXONOMY_API_AUTH")
    monkeypatch.setenv("TAXONOMY_API_TOKEN", TOKEN)
    return TestClient(app)


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _payload(path: Path | str) -> dict:
    return {
        "datasets": [{"name": "original", "paths": [str(path)]}],
        "metrics": [{"id": "integrity", "params": {"dataset": "original"}}],
    }


def test_requests_without_a_token_are_refused(secured: TestClient) -> None:
    response = secured.get("/metrics")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Bearer wrong-token", "Basic dGVzdC10b2tlbg==", TOKEN, "Bearer"])
def test_requests_with_a_wrong_or_malformed_token_are_refused(secured: TestClient, header: str) -> None:
    assert secured.get("/metrics", headers={"Authorization": header}).status_code == 401


def test_requests_with_the_configured_token_are_served(secured: TestClient) -> None:
    assert secured.get("/metrics", headers=_auth()).status_code == 200


def test_every_route_needs_the_token_not_just_run_creation(secured: TestClient, isolated_db) -> None:
    for path in (
        "/runs", "/runs/abc", "/taxonomy/search?run_id=a&dataset=b", "/taxonomy/tags/1?run_id=a&dataset=b",
        "/proposals?merge_run=x", "/proposals/p1?merge_run=x",  # routers added later are covered too
    ):
        assert secured.get(path).status_code == 401, path


@pytest.mark.parametrize("placeholder", sorted(PLACEHOLDER_TOKENS))
def test_the_env_example_placeholder_is_not_accepted_as_a_token(
    monkeypatch: pytest.MonkeyPatch, placeholder: str,
) -> None:
    monkeypatch.delenv("TAXONOMY_API_AUTH")
    monkeypatch.setenv("TAXONOMY_API_TOKEN", placeholder)

    response = TestClient(app).get("/metrics", headers=_auth(placeholder))

    assert response.status_code == 401


def test_an_unconfigured_token_refuses_every_request_instead_of_opening_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAXONOMY_API_AUTH")
    monkeypatch.delenv("TAXONOMY_API_TOKEN", raising=False)
    client = TestClient(app)

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer "}).status_code == 401


def test_auth_can_be_switched_off_explicitly_for_local_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAXONOMY_API_AUTH", "off")
    monkeypatch.delenv("TAXONOMY_API_TOKEN", raising=False)

    assert TestClient(app).get("/metrics").status_code == 200


def test_runs_may_read_datasets_inside_the_data_roots(secured: TestClient, isolated_db) -> None:
    response = secured.post("/runs", json=_payload(FIXTURES / "mini_original.sql"), headers=_auth())

    assert response.status_code == 202


def test_runs_naming_a_file_outside_the_data_roots_are_rejected(secured: TestClient, isolated_db) -> None:
    response = secured.post("/runs", json=_payload("/etc/passwd"), headers=_auth())

    assert response.status_code == 422
    assert response.json()["detail"]["disallowed_paths"] == [{"dataset": "original", "path": "/etc/passwd"}]


def test_dot_dot_segments_cannot_climb_out_of_a_data_root(
    secured: TestClient, isolated_db, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    (tmp_path / "secret.sql").write_text("")
    monkeypatch.setenv("TAXONOMY_DATA_ROOTS", str(root))

    response = secured.post("/runs", json=_payload(f"{root}/../secret.sql"), headers=_auth())

    assert response.status_code == 422


def test_a_symlink_inside_a_data_root_cannot_point_outside_it(
    secured: TestClient, isolated_db, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "secret.sql"
    outside.write_text("")
    (root / "link.sql").symlink_to(outside)
    monkeypatch.setenv("TAXONOMY_DATA_ROOTS", str(root))

    response = secured.post("/runs", json=_payload(root / "link.sql"), headers=_auth())

    assert response.status_code == 422


def test_a_sibling_directory_sharing_the_roots_name_prefix_is_outside_it(
    secured: TestClient, isolated_db, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    (tmp_path / "data").mkdir()
    sibling = tmp_path / "data-private"
    sibling.mkdir()
    (sibling / "secret.sql").write_text("")
    monkeypatch.setenv("TAXONOMY_DATA_ROOTS", str(tmp_path / "data"))

    response = secured.post("/runs", json=_payload(sibling / "secret.sql"), headers=_auth())

    assert response.status_code == 422


def test_data_roots_default_to_the_backend_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXONOMY_DATA_ROOTS")

    assert data_roots() == [DEFAULT_DATA_ROOT]
    assert (DEFAULT_DATA_ROOT / "sql-files-nus").is_dir()


def test_runs_cannot_enable_the_llm_judge_through_the_api(secured: TestClient, isolated_db) -> None:
    payload = _payload(FIXTURES / "mini_original.sql")
    payload["metrics"].append({"id": "saturation", "params": {
        "baseline": "original", "incoming": ["original"], "semantic_model": "m",
        "judge": {"model": "gpt-x", "cache": "/tmp/anywhere/judge_cache.json"},
    }})

    response = secured.post("/runs", json=payload, headers=_auth())

    assert response.status_code == 422
    assert response.json()["detail"]["judged_metrics"] == [{"metric_index": 1, "metric_id": "saturation"}]
    assert db.list_runs() == []  # refused before anything was queued


def test_runs_with_the_judge_explicitly_off_are_accepted(secured: TestClient, isolated_db) -> None:
    payload = _payload(FIXTURES / "mini_original.sql")
    payload["metrics"].append({"id": "saturation", "params": {
        "baseline": "original", "incoming": ["original"], "judge": None,
    }})

    assert secured.post("/runs", json=payload, headers=_auth()).status_code == 202
