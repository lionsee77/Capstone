"""The proposal API shares the CLI view and confines file access to the configured merge root."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from taxonomy_pipeline.api.app import app
from taxonomy_pipeline.api.proposals import _merge_directory
from taxonomy_pipeline.cli import main


# API list/detail and CLI return identical evidence while leaving input, baseline and audit bytes unchanged.
def test_api_and_cli_share_read_only_proposal_view(
    saved_proposal_run: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TAXONOMY_MERGE_ROOT", str(saved_proposal_run.parent))
    files = {path: path.read_bytes() for path in saved_proposal_run.parents[1].rglob("*") if path.is_file()}
    client = TestClient(app)
    response = client.get("/proposals", params={"merge_run": "reviewed", "level": "speciality", "limit": 1})
    assert response.status_code == 200
    assert main(["review-proposals", "--merge-run", str(saved_proposal_run), "--level", "speciality",
                 "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out) == response.json()
    proposal_id = response.json()["proposals"][0]["proposal"]["proposal_id"]
    detail = client.get(f"/proposals/{proposal_id}", params={"merge_run": "reviewed"})
    assert main(["review-proposals", "--merge-run", str(saved_proposal_run), "--proposal-id", proposal_id]) == 0
    assert detail.status_code == 200 and detail.json() == json.loads(capsys.readouterr().out)
    assert detail.json() == response.json()
    assert {path: path.read_bytes() for path in saved_proposal_run.parents[1].rglob("*") if path.is_file()} == files


# Missing runs and proposals are distinct from malformed requests and unreadable saved screening.
def test_proposal_api_reports_missing_and_invalid_requests(
    saved_proposal_run: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TAXONOMY_MERGE_ROOT", str(saved_proposal_run.parent))
    client = TestClient(app)
    assert client.get("/proposals", params={"merge_run": "missing"}).status_code == 404
    assert client.get("/proposals/missing", params={"merge_run": "reviewed"}).status_code == 404
    for query in ({"limit": 0}, {"limit": 201}, {"offset": -1}, {"level": "tag"}):
        assert client.get("/proposals", params={"merge_run": "reviewed", **query}).status_code == 422
    assert main(["review-proposals", "--merge-run", str(saved_proposal_run), "--proposal-id", "missing"]) == 1
    assert "not found" in capsys.readouterr().err
    resolution = saved_proposal_run / "resolution.json"
    content = json.loads(resolution.read_text(encoding="utf-8"))
    content["screening"] = None
    resolution.write_text(json.dumps(content), encoding="utf-8")
    assert client.get("/proposals", params={"merge_run": "reviewed"}).status_code == 409
    assert main(["review-proposals", "--merge-run", str(saved_proposal_run)]) == 1
    assert "rerun merge-preview" in capsys.readouterr().err
    resolution.write_text("broken json", encoding="utf-8")
    assert client.get("/proposals", params={"merge_run": "reviewed"}).status_code == 409


@pytest.mark.parametrize("directory", ["../reviewed", "..", "C:\\data", "/tmp/run", "a/b", "a\\b"])
# The HTTP boundary accepts run names rather than arbitrary server paths on either operating system.
def test_proposal_api_rejects_path_traversal(directory: str) -> None:
    assert TestClient(app).get("/proposals", params={"merge_run": directory}).status_code == 422


# Resolving a symlink outside the root must be rejected even when its public name is valid.
def test_proposal_api_rejects_resolved_paths_outside_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "merge_runs"
    monkeypatch.setenv("TAXONOMY_MERGE_ROOT", str(root))
    original = Path.resolve

    # Simulate symlink resolution without requiring Windows symlink privileges in the test runner.
    def resolve(path: Path, *args: object, **kwargs: object) -> Path:
        return tmp_path / "outside" if path == root / "linked" else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(HTTPException) as error:
        _merge_directory("linked")
    assert error.value.status_code == 404
