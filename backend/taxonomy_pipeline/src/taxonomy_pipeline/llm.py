"""The one place an OpenAI client is built, for the saturation judge and Case B.

Both need the same setup: `backend/.env` loaded (through `env.load_env`, so a
variable set in the shell still wins), a real `OPENAI_API_KEY` rather than the
`.env.example` placeholder, and `OPENAI_BASE_URL` for a proxy or compatible
endpoint. Keeping it here means a new setting (an organisation ID, a timeout)
is added once. Callers wrap `LLMError` in their own error type, so their
callers keep catching what they caught before.

The OpenAI SDK is the optional `llm` extra and is imported only when a client is
built, so modules that use this one still import without it.
"""
from __future__ import annotations

import os
from typing import Any

from . import env

DEFAULT_BASE_URL = "https://api.openai.com/v1"
# The values `.env.example` ships with; a key left at one of them is treated as missing.
PLACEHOLDER_KEYS = frozenset({"your-openai-api-key", "your-api-key"})


class LLMError(RuntimeError):
    """No client can be built (missing key or dependency); the message says what to fix."""


def base_url() -> str:
    """The endpoint clients talk to: `OPENAI_BASE_URL`, or OpenAI's own."""
    return os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL).strip()


def openai_client(purpose: str, *, async_client: bool = False, **client_kwargs: Any) -> Any:
    """An `OpenAI` (or `AsyncOpenAI`) client; `purpose` names the caller in error messages.

    `client_kwargs` go to the client constructor (e.g. `max_retries`).
    """
    env.load_env()
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key or api_key in PLACEHOLDER_KEYS:
        raise LLMError(f"{purpose} needs a real OPENAI_API_KEY in {env.ENV_FILE} or the environment")
    try:
        from openai import AsyncOpenAI, OpenAI
    except ImportError as exc:
        raise LLMError(f'{purpose} needs the optional LLM dependencies: pip install -e ".[llm]"') from exc
    client_class = AsyncOpenAI if async_client else OpenAI
    return client_class(api_key=api_key, base_url=base_url(), **client_kwargs)
