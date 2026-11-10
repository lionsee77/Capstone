"""`GET /lookup` as a thin adapter: its root setting, parameters and error statuses."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxonomy_pipeline.api.app import app
from taxonomy_pipeline.app import lookup
from tests.conftest import FIXTURES

# Lookups read approved synonyms; never let them see the developer's real database.
pytestmark = pytest.mark.usefixtures("isolated_db")

@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, integration_round: Callable[..., Path]) -> TestClient:
    baseline = (FIXTURES / "mini_original.sql").read_text(encoding="utf-8")
    integration_round("r1", {"mini.sql": baseline}, "INSERT INTO `tags` (`id`, `name`) VALUES (200, 'Payroll');\n")
    monkeypatch.setenv("TAXONOMY_INTEGRATIONS_ROOT", str(tmp_path / "integrations"))
    return TestClient(app)


def test_returns_exact_and_near_matches_for_a_term_without_a_run_id(client: TestClient) -> None:
    response = client.get("/lookup", params={"q": "Tax Advisory"})

    assert response.status_code == 200
    body = response.json()
    assert (body["query"], body["snapshot"], body["exists"]) == ("Tax Advisory", "r1", True)
    assert body["near"] == []
    assert body["exact"][0] == {
        "id": 10, "name": "Tax Advisory", "level": "specialities", "match_type": "exact",
        "lexical_score": 100.0, "semantic_score": None, "synonym": None, "active": True,
        "paths": [{
            "service": {"id": 1, "name": "Professional Services"},
            "category": {"id": 1, "name": "Accounting"},
            "speciality": {"id": 10, "name": "Tax Advisory"},
        }],
    }


def test_passes_its_filters_to_the_use_case(client: TestClient) -> None:
    params = {"q": "Accountng", "level": "categories", "limit": 1, "min_score": 80}

    body = client.get("/lookup", params=params).json()

    assert [match["level"] for match in body["near"]] == ["categories"]


def test_semantic_is_off_unless_requested_and_reports_503_when_it_cannot_load(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> None:
        raise OSError("/private/server/path")

    monkeypatch.setattr(lookup, "_load_matcher", fail)

    assert client.get("/lookup", params={"q": "tax"}).status_code == 200
    response = client.get("/lookup", params={"q": "tax", "semantic": "true"})
    assert response.status_code == 503
    assert "/private" not in response.text


@pytest.mark.parametrize(
    "params",
    [
        {}, {"q": ""}, {"q": "!!!"}, {"q": "x" * 201}, {"q": "tax", "level": "industries"},
        {"q": "tax", "limit": 0}, {"q": "tax", "limit": 101}, {"q": "tax", "min_score": 101},
        {"q": "tax", "snapshot": "../r1"}, {"q": "tax", "snapshot": "/etc"},
    ],
)
def test_rejects_invalid_parameters(client: TestClient, params: dict) -> None:
    assert client.get("/lookup", params=params).status_code == 422


def test_an_unknown_snapshot_is_404(client: TestClient) -> None:
    assert client.get("/lookup", params={"q": "tax", "snapshot": "nope"}).status_code == 404


def test_no_snapshots_is_409_without_exposing_the_server_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAXONOMY_INTEGRATIONS_ROOT", str(tmp_path / "none"))

    response = TestClient(app).get("/lookup", params={"q": "tax"})

    assert response.status_code == 409
    assert str(tmp_path) not in response.text


def test_an_unreadable_synonym_store_is_503_not_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    def broken(*args: object, **kwargs: object) -> list:
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(lookup, "approved_synonyms", broken)
    response = client.get("/lookup", params={"q": "Tax Advisory"})
    assert response.status_code == 503 and "synonym store" in response.json()["detail"]
