"""Load `backend/.env` into the process environment for commands that need its settings.

`taxonomy-pipeline serve` reads its API settings (`TAXONOMY_API_TOKEN`,
`TAXONOMY_API_AUTH`, `TAXONOMY_DATA_ROOTS`) from the environment, so it loads
this file first; without that, settings written only in `backend/.env` would be
ignored and every request refused. Variables already set in the shell win over
the file, so a one-off `TAXONOMY_API_AUTH=off taxonomy-pipeline serve` still
works.

Case B and the saturation judge load it too, through `llm.py`, so this is the
only place that knows where the file lives.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# backend/.env when installed in editable mode (src/taxonomy_pipeline/env.py -> backend/).
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def load_env(path: Path | None = None) -> bool:
    """Load `path` (default `ENV_FILE`) without overriding set variables; False when there is no file."""
    path = path or ENV_FILE
    if not path.is_file():
        return False
    load_dotenv(path, override=False)
    return True
