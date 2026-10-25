"""Access control for the HTTP API: a shared bearer token, and which server paths runs may read.

The API starts background runs and reads whatever dataset files a run config
names, so it must not be open to anyone who can reach the port. Two checks:

- `require_token` is attached to the whole app, so every route - including
  routers added later - needs `Authorization: Bearer <TAXONOMY_API_TOKEN>`.
  It fails closed: with no token configured every request is refused, unless
  `TAXONOMY_API_AUTH=off` deliberately turns the check off for local
  development. A forgotten setting therefore shows up as 401s instead of an
  open API. The `.env.example` placeholder (`PLACEHOLDER_TOKENS`) counts as
  not configured, so copying the example unchanged doesn't give everyone the
  same known token.
- `disallowed_paths` limits `POST /runs` to dataset files inside
  `TAXONOMY_DATA_ROOTS` (`os.pathsep`-separated; default: the `backend/`
  directory this package sits in). Paths are resolved first, so `..` and
  symlinks can't escape a root.

Settings are read per request, not at import, so tests and a restarted
process see the current environment.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException

from ..config import PipelineConfig

# backend/ when installed in editable mode (src/taxonomy_pipeline/api/security.py -> backend/).
DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[4]
# Values shipped in the .env.example files; a token everyone can read in the repo protects nothing.
PLACEHOLDER_TOKENS = frozenset({"change-me"})


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Reject the request unless it carries the configured bearer token."""
    if os.environ.get("TAXONOMY_API_AUTH", "").strip().lower() == "off":
        return
    expected = os.environ.get("TAXONOMY_API_TOKEN", "").strip()
    if expected in PLACEHOLDER_TOKENS:
        expected = ""
    scheme, _, supplied = (authorization or "").partition(" ")
    # compare_digest keeps the comparison time independent of how much of the token matched.
    if not expected or scheme.lower() != "bearer" or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status_code=401, detail="Missing or invalid API token", headers={"WWW-Authenticate": "Bearer"},
        )


def data_roots() -> list[Path]:
    configured = [entry for entry in os.environ.get("TAXONOMY_DATA_ROOTS", "").split(os.pathsep) if entry.strip()]
    return [Path(entry).expanduser().resolve() for entry in configured] or [DEFAULT_DATA_ROOT]


def disallowed_paths(config: PipelineConfig) -> list[dict[str, str]]:
    """Every dataset path in `config` that resolves outside the allowed data roots."""
    roots = data_roots()
    return [
        {"dataset": dataset.name, "path": path}
        for dataset in config.datasets
        for path in dataset.paths
        if not any(Path(path).resolve().is_relative_to(root) for root in roots)
    ]
