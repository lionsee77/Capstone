from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from taxonomy_pipeline.matching.judge import (
    NONE_CHOICE,
    QUESTION_NAME,
    DecisionsJudge,
    JudgeCandidate,
    JudgeError,
    JudgeQuestion,
    build_request,
    parse_response,
)

QUESTION = JudgeQuestion(
    name="Trade Finance Management", level="tags", parents=("Trade Finance Manager",),
    candidates=(JudgeCandidate("Corporate Finance", "specialities", ("Finance",)),
                JudgeCandidate("Trade Marketing", "specialities")),
)
SECOND = JudgeQuestion("Render Management", "tags", (), (JudgeCandidate("Vendor Management", "specialities"),))


def _answer(choice: str, probabilities: dict[str, float] | None = None, **extra: Any) -> dict[str, Any]:
    probabilities = probabilities or {choice: 0.9}
    return {"answers": [{
        "type": "choice", "name": QUESTION_NAME, "choice": choice, "confidence": 0.85,
        "probabilities": [{"value": v, "probability": p} for v, p in probabilities.items()], **extra,
    }]}


class FakeClient:
    """Returns (or raises) the given responses in call order and records each request."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def post(self, path: str, *, body: dict[str, Any], cast_to: type) -> Any:
        assert path == "/decisions" and cast_to is object
        self.requests.append(body)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class StatusError(Exception):
    """Stands in for openai.APIStatusError, which the judge reads duck-typed."""

    def __init__(self, status_code: int) -> None:
        super().__init__("boom")
        self.status_code, self.message = status_code, "boom"


def test_request_numbers_each_candidate_and_offers_none() -> None:
    request = build_request("gpt-6-luna", QUESTION)
    question = request["questions"][0]
    assert request["model"] == "gpt-6-luna"
    assert question["type"] == "choice" and question["name"] == QUESTION_NAME
    assert [c["value"] for c in question["choices"]] == ["1", "2", NONE_CHOICE]
    assert '1. "Corporate Finance" (speciality; under: Finance)' in request["input"]
    assert '"Trade Finance Management" (tag; under: Trade Finance Manager)' in request["input"]


def test_reads_the_chosen_candidate_and_its_probability() -> None:
    verdict = parse_response(_answer("1", {"1": 0.92, "2": 0.03, "none": 0.05}), build_request("m", QUESTION))
    assert verdict.choice == "1" and verdict.candidate_index == 0
    assert verdict.probability == 0.92 and verdict.confidence == 0.85 and verdict.error is None


def test_none_has_no_candidate_index() -> None:
    verdict = parse_response(_answer(NONE_CHOICE), build_request("m", QUESTION))
    assert verdict.choice == NONE_CHOICE and verdict.candidate_index is None and verdict.probability == 0.9


@pytest.mark.parametrize("response", [
    {"answers": [{"type": "refusal", "name": QUESTION_NAME}]},
    {"answers": []},
    {"unexpected": True},
    _answer("7"),
])
def test_refusals_and_malformed_answers_become_error_verdicts(response: dict[str, Any]) -> None:
    verdict = parse_response(response, build_request("m", QUESTION))
    assert verdict.error and verdict.choice is None


def test_caches_verdicts_so_a_rerun_makes_no_requests(tmp_path: Path) -> None:
    cache = tmp_path / "judge_cache.json"
    first = DecisionsJudge("gpt-6-luna", cache_path=cache, client=FakeClient([_answer("1")]))
    assert first.judge([QUESTION])[0].choice == "1"

    rerun_client = FakeClient([])
    rerun = DecisionsJudge("gpt-6-luna", cache_path=cache, client=rerun_client)
    assert rerun.judge([QUESTION])[0].choice == "1"
    assert rerun_client.requests == []


def test_a_different_model_is_not_served_from_the_cache(tmp_path: Path) -> None:
    cache = tmp_path / "judge_cache.json"
    DecisionsJudge("gpt-6-luna", cache_path=cache, client=FakeClient([_answer("1")])).judge([QUESTION])
    other = FakeClient([_answer(NONE_CHOICE)])
    assert DecisionsJudge("other-model", cache_path=cache, client=other).judge([QUESTION])[0].choice == NONE_CHOICE
    assert len(other.requests) == 1


def test_a_transient_failure_is_an_error_verdict_and_is_not_cached(tmp_path: Path) -> None:
    cache = tmp_path / "judge_cache.json"
    verdict = DecisionsJudge("m", cache_path=cache, client=FakeClient([StatusError(500)])).judge([QUESTION])[0]
    assert verdict.error == "API error 500: boom"
    retry = FakeClient([_answer("2")])
    assert DecisionsJudge("m", cache_path=cache, client=retry).judge([QUESTION])[0].choice == "2"


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_a_rejected_request_stops_the_run(code: int) -> None:
    with pytest.raises(JudgeError, match=str(code)):
        DecisionsJudge("m", client=FakeClient([StatusError(code)])).judge([QUESTION])


def test_answers_paid_for_before_a_rejection_are_still_cached(tmp_path: Path) -> None:
    cache = tmp_path / "judge_cache.json"
    judge = DecisionsJudge("m", workers=1, cache_path=cache, client=FakeClient([StatusError(401), _answer("none")]))
    with pytest.raises(JudgeError, match="401"):
        judge.judge([QUESTION, SECOND])

    rerun_client = FakeClient([])
    assert DecisionsJudge("m", cache_path=cache, client=rerun_client).judge([SECOND])[0].choice == NONE_CHOICE
    assert rerun_client.requests == []


def test_an_interrupted_cache_write_keeps_the_previous_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "judge_cache.json"
    DecisionsJudge("m", cache_path=cache, client=FakeClient([_answer("1")])).judge([QUESTION])
    before = cache.read_text(encoding="utf-8")

    real_write = Path.write_text

    def interrupted(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        real_write(self, data[: len(data) // 2], *args, **kwargs)  # half the file lands, then the write dies
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", interrupted)
    with pytest.raises(OSError, match="disk full"):
        DecisionsJudge("m", cache_path=cache, client=FakeClient([_answer("none")])).judge([SECOND])
    assert cache.read_text(encoding="utf-8") == before


def test_verdicts_come_back_in_question_order() -> None:
    client = FakeClient([_answer("1"), _answer(NONE_CHOICE)])
    verdicts = DecisionsJudge("m", workers=1, client=client).judge([QUESTION, SECOND])
    assert [v.choice for v in verdicts] == ["1", NONE_CHOICE]


def test_an_unreadable_cache_is_refused(tmp_path: Path) -> None:
    cache = tmp_path / "judge_cache.json"
    cache.write_text("not json", encoding="utf-8")
    with pytest.raises(JudgeError, match="unreadable"):
        DecisionsJudge("m", cache_path=cache, client=FakeClient([]))


def test_a_missing_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from taxonomy_pipeline.matching import judge

    monkeypatch.setattr(judge, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(JudgeError, match="OPENAI_API_KEY"):
        DecisionsJudge("m")


def test_a_connection_failure_is_an_error_verdict() -> None:
    verdict = DecisionsJudge("m", client=FakeClient([ConnectionError("offline")])).judge([QUESTION])[0]
    assert verdict.error == "ConnectionError: offline"
