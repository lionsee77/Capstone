from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxonomy_pipeline.api import lineage
from taxonomy_pipeline.api.app import app


def test_lineage_reads_the_configured_integrations_and_merge_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, integration_round: Callable[..., Path],
) -> None:
    integration_round("round-1", {"tags.sql": "x"}, "y")
    monkeypatch.setenv("TAXONOMY_INTEGRATIONS_ROOT", str(tmp_path / "integrations"))
    monkeypatch.setenv("TAXONOMY_MERGE_ROOT", str(tmp_path / "merge_runs"))

    body = TestClient(app).get("/lineage").json()

    assert [stage["id"] for stage in body["stages"]] == ["baseline:original", "round-1"]
    assert body["stages"][1]["sources"] == ["skillsfuture"]
    assert body["chains"] == [{"stages": ["baseline:original", "round-1"], "runs": []}]


def test_lineage_with_no_integrations_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAXONOMY_INTEGRATIONS_ROOT", str(tmp_path / "none"))

    assert TestClient(app).get("/lineage").json() == {"stages": [], "chains": [], "runs": []}


def test_default_integrations_root_sits_beside_merge_runs() -> None:
    assert lineage.DEFAULT_INTEGRATIONS_ROOT.name == "integrations"
    assert lineage.DEFAULT_INTEGRATIONS_ROOT.parent == lineage.DEFAULT_MERGE_ROOT.parent


def test_lineage_route_declares_its_response_model() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    schema = paths["/lineage"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/TaxonomyLineage")
