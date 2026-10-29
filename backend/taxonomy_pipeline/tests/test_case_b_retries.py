"""Exercise retry budgets without making API calls or waiting in tests."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from typing import Any

import pytest

from taxonomy_pipeline.enrichment import case_b

PROPOSAL = {
    "tag": "Skill", "tag_id": "1", "action": "assign_existing", "service": "Subject",
    "category": "Finance", "speciality": "Analysis", "confidence": 0.8, "reason": "Matches",
}


class RequestFailure(Exception):
    """Represent HTTP errors without requiring the optional OpenAI SDK."""

    def __init__(self, status: int, retry_after: str | None = None, code: str | None = None) -> None:
        self.status_code = status
        self.code = code
        self.response = SimpleNamespace(headers={} if retry_after is None else {"retry-after": retry_after})


class Client:
    """Return scripted errors and JSON, and record the SDK retry configuration."""

    def __init__(self, *events: Any) -> None:
        self.events = list(events)
        self.calls = 0
        self.options: dict[str, Any] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, **options: Any) -> Client:
        self.options = options
        return self

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=event))])


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(case_b.asyncio, "sleep", sleep)
    monkeypatch.setattr(case_b.random, "uniform", lambda *_: 0)
    return recorded


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_errors_are_attempted_once(status: int, waits: list[float]) -> None:
    client = Client(RequestFailure(status))
    with pytest.raises(case_b.CaseBError, match="not retrying"):
        asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}], "model"))
    assert client.calls == 1
    assert waits == []


@pytest.mark.parametrize("error", [RequestFailure(429), RequestFailure(503), ConnectionError(), TimeoutError()])
def test_transient_errors_have_three_attempts_with_backoff(error: Exception, waits: list[float]) -> None:
    client = Client(error, error, error)
    with pytest.raises(case_b.CaseBError, match="after 3 attempts"):
        asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}], "model"))
    assert client.calls == 3
    assert waits == [1, 2]
    assert client.options == {"max_retries": 0}


def test_retry_after_controls_the_delay_before_recovery(waits: list[float]) -> None:
    client = Client(RequestFailure(429, "7"), json.dumps({"proposals": [PROPOSAL]}))
    assert asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}], "model")) == [PROPOSAL]
    assert waits == [7]


def test_long_retry_after_stops_without_sleeping_or_retrying(waits: list[float]) -> None:
    client = Client(RequestFailure(429, "120"))
    with pytest.raises(case_b.CaseBError, match="above 60 seconds"):
        asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}], "model"))
    assert client.calls == 1
    assert waits == []


def test_retry_after_parses_http_dates() -> None:
    date = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    assert 28 <= case_b._retry_delay(RequestFailure(429, date), 1) <= 30


def test_quota_errors_are_not_retried(waits: list[float]) -> None:
    client = Client(RequestFailure(429, code="insufficient_quota"))
    with pytest.raises(case_b.CaseBError, match="not retrying"):
        asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}], "model"))
    assert client.calls == 1
    assert waits == []


def test_partial_results_survive_malformed_json_and_transient_failures(waits: list[float]) -> None:
    other = {**PROPOSAL, "tag_id": "2"}
    client = Client(json.dumps({"proposals": [PROPOSAL]}), RequestFailure(503), json.dumps({"proposals": [other]}))
    result = asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}, {"tag_id": "2"}], "model"))
    assert result == [PROPOSAL, other]


def test_malformed_json_can_recover(waits: list[float]) -> None:
    client = Client("[]", "not JSON", json.dumps({"proposals": [PROPOSAL]}))
    assert asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}], "model")) == [PROPOSAL]
