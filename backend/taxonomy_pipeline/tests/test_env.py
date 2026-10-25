"""Test that `serve` picks up API settings from backend/.env without overriding the shell."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from taxonomy_pipeline import env
from taxonomy_pipeline.cli import main


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    monkeypatch.setattr(env, "ENV_FILE", path)
    return path


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """Stand in for uvicorn.run, recording the API settings the server would start with."""
    seen: dict[str, str | None] = {}

    def run(*_: object, **__: object) -> None:
        seen.update({name: os.environ.get(name) for name in ("TAXONOMY_API_TOKEN", "TAXONOMY_API_AUTH")})

    monkeypatch.setattr("uvicorn.run", run)
    return seen


def test_serve_reads_the_api_token_from_the_env_file(env_file: Path, served, monkeypatch) -> None:
    monkeypatch.delenv("TAXONOMY_API_TOKEN", raising=False)
    env_file.write_text("TAXONOMY_API_TOKEN=from-file\n")

    try:
        assert main(["serve"]) == 0
        assert served["TAXONOMY_API_TOKEN"] == "from-file"
    finally:
        # load_dotenv set it directly, outside monkeypatch's undo list, so remove it here.
        os.environ.pop("TAXONOMY_API_TOKEN", None)


def test_a_variable_already_set_in_the_shell_wins_over_the_env_file(env_file: Path, served, monkeypatch) -> None:
    monkeypatch.setenv("TAXONOMY_API_AUTH", "on")
    env_file.write_text("TAXONOMY_API_AUTH=off\n")

    assert main(["serve"]) == 0
    assert served["TAXONOMY_API_AUTH"] == "on"


def test_serve_starts_without_an_env_file(env_file: Path, served) -> None:
    assert not env_file.exists()
    assert env.load_env() is False
    assert main(["serve"]) == 0


def test_the_env_file_is_backend_dot_env() -> None:
    assert env.ENV_FILE.name == ".env"
    assert (env.ENV_FILE.parent / "taxonomy_pipeline" / "pyproject.toml").is_file()
