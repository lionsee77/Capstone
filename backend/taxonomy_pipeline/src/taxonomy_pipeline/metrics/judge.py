"""An LLM judge for the concepts saturation's similarity scores can't settle.

Saturation's exact/spelling/meaning checks are cheap enough to compare every
incoming concept with every known one, but a middle-band score can't tell "the
same skill, worded differently" from "a different skill in the same field".
Saturation can optionally ask a judge about each `REVIEW_AMBIGUOUS` concept: here
is the concept and its closest known concepts (found by the meaning model), each
with its parents as context - is it the same concept as one of them, or none?

`ConceptJudge` is the small protocol saturation depends on; `DecisionsJudge`
implements it with OpenAI's Decisions API (`POST /v1/decisions`, public beta),
which answers a `choice` question with a probability per option. Another judge
(a different provider, or a fake in tests) only has to implement `judge`.

Verdicts are cached on disk, keyed by the exact request, so rerunning a report
gives the same numbers without paying again; a changed model, prompt or
candidate list is a different key. The OpenAI SDK is the optional `llm` extra and
is imported only when a `DecisionsJudge` builds its own client.
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

# backend/.env, the same file Case B reads its key from.
ENV_FILE = Path(__file__).resolve().parents[4] / ".env"
QUESTION_NAME = "same_concept"
NONE_CHOICE = "none"
# Bump when the wording below changes, so cached verdicts for the old wording are not reused.
PROMPT_VERSION = 1
INSTRUCTIONS = (
    "Is the incoming concept the same concept as one of the numbered candidates from the existing taxonomy - "
    "the same skill, role or sector under a different wording, or a synonym? If so, choose that candidate's "
    "number. Choose none if every candidate is only related: broader, narrower, a different skill in the same "
    "field, or merely a similar-sounding name. Use the parents listed for each concept as context."
)
# Bad request, key, permission, or unknown model/endpoint: every other request would fail the same way.
REJECTED_STATUSES = {400, 401, 403, 404}
_LEVEL_NOUNS = {"categories": "category", "specialities": "speciality", "tags": "tag"}


class JudgeError(RuntimeError):
    """The judge can't run at all (missing dependency or key, rejected request); retrying won't help."""


@dataclass(frozen=True)
class JudgeCandidate:
    """A known concept offered to the judge, with the parents it sits under."""

    name: str
    level: str
    parents: tuple[str, ...] = ()


@dataclass(frozen=True)
class JudgeQuestion:
    """One ambiguous concept, the parents it has in its own source, and its closest known concepts."""

    name: str
    level: str
    parents: tuple[str, ...]
    candidates: tuple[JudgeCandidate, ...]


@dataclass
class JudgeVerdict:
    """The judge's answer: a candidate number ("1".."k"), `none`, or an error instead."""

    choice: str | None = None
    probability: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    error: str | None = None

    @property
    def candidate_index(self) -> int | None:
        """Zero-based index of the chosen candidate, or None for `none` and errors."""
        return int(self.choice) - 1 if self.choice and self.choice.isdigit() else None


class ConceptJudge(Protocol):
    model: str

    def judge(self, questions: list[JudgeQuestion]) -> list[JudgeVerdict]:
        """One verdict per question, in order."""
        ...


def _describe(name: str, level: str, parents: tuple[str, ...]) -> str:
    noun = _LEVEL_NOUNS.get(level, level)
    return f'"{name}" ({noun}; under: {", ".join(parents)})' if parents else f'"{name}" ({noun})'


def build_request(model: str, question: JudgeQuestion) -> dict[str, Any]:
    """The Decisions API request for one question."""
    lines = [f"Incoming concept: {_describe(question.name, question.level, question.parents)}",
             "Candidates from the existing taxonomy:"]
    lines += [f"{number}. {_describe(c.name, c.level, c.parents)}"
              for number, c in enumerate(question.candidates, start=1)]
    choices = [{"value": str(number), "description": f"Same concept as candidate {number}: {c.name}"}
               for number, c in enumerate(question.candidates, start=1)]
    choices.append({"value": NONE_CHOICE, "description": "None of the candidates is the same concept."})
    return {
        "model": model,
        "input": "\n".join(lines),
        "questions": [{"type": "choice", "name": QUESTION_NAME, "instructions": INSTRUCTIONS, "choices": choices}],
    }


def parse_response(response: Any, request: dict[str, Any]) -> JudgeVerdict:
    """Read the answer to our one question; anything unexpected becomes an error verdict."""
    allowed = {choice["value"] for choice in request["questions"][0]["choices"]}
    try:
        answer = next(a for a in response["answers"] if a.get("name") == QUESTION_NAME)
    except (KeyError, TypeError, StopIteration):
        return JudgeVerdict(error="response has no answer to the question")
    if answer.get("type") == "refusal":
        return JudgeVerdict(error="the model refused to answer")
    choice = answer.get("choice")
    if answer.get("type") != "choice" or choice not in allowed:
        return JudgeVerdict(error=f"unexpected answer {answer.get('type')!r} / {choice!r}")
    try:
        probabilities = {str(p["value"]): float(p["probability"]) for p in answer.get("probabilities", [])}
        confidence = float(answer["confidence"]) if answer.get("confidence") is not None else None
    except (KeyError, TypeError, ValueError):
        return JudgeVerdict(error="malformed probabilities")
    return JudgeVerdict(choice=choice, probability=probabilities.get(choice), probabilities=probabilities,
                        confidence=confidence)


def _openai_client(max_retries: int) -> Any:
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass  # without python-dotenv the key can still come from the environment
    else:
        load_dotenv(ENV_FILE)
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key or api_key in {"your-openai-api-key", "your-api-key"}:
        raise JudgeError(f"the saturation judge needs a real OPENAI_API_KEY in {ENV_FILE} or the environment")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise JudgeError('the saturation judge needs the optional LLM dependencies: pip install -e ".[llm]"') from exc
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    return OpenAI(api_key=api_key, base_url=base_url, max_retries=max_retries)


class DecisionsJudge:
    """Asks OpenAI's Decisions API one `choice` question per concept, in parallel, with an on-disk cache.

    The installed SDK (`openai` 1.x) has no `decisions` resource, so the request
    goes through the SDK's generic `post`, which still handles auth, retries with
    backoff on rate limits/timeouts/5xx, and errors. A request the API rejects
    outright (bad key, unknown model or endpoint, invalid body) raises
    `JudgeError` and stops the run, since every other request would fail the same
    way; any other failure becomes an error verdict for that concept alone.
    """

    def __init__(self, model: str, *, workers: int = 8, cache_path: str | Path | None = None,
                 max_retries: int = 3, client: Any = None) -> None:
        self.model = model
        self.workers = workers
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache = self._load_cache()
        self.client = client if client is not None else _openai_client(max_retries)

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JudgeError(f"judge cache {self.cache_path} is unreadable ({exc}); delete it to start fresh") from exc

    def _save_cache(self) -> None:
        if self.cache_path:
            # Write beside the cache, then swap it in, so an interrupted write never truncates paid answers.
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.cache_path.with_name(self.cache_path.name + ".tmp")
            partial.write_text(json.dumps(self._cache, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(partial, self.cache_path)

    @staticmethod
    def _key(request: dict[str, Any]) -> str:
        payload = json.dumps({"prompt_version": PROMPT_VERSION, **request}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _ask(self, request: dict[str, Any]) -> JudgeVerdict:
        try:
            response = self.client.post("/decisions", body=request, cast_to=object)
        except Exception as exc:  # noqa: BLE001 - the SDK has already retried transient errors
            # Read the status duck-typed, so this module needs no openai import of its own.
            status = getattr(exc, "status_code", None)
            message = getattr(exc, "message", None) or str(exc)
            if status in REJECTED_STATUSES:
                raise JudgeError(f"Decisions API rejected the request ({status}): {message}") from exc
            if status:
                return JudgeVerdict(error=f"API error {status}: {message}")
            return JudgeVerdict(error=f"{type(exc).__name__}: {message}")
        return parse_response(response, request)

    def judge(self, questions: list[JudgeQuestion]) -> list[JudgeVerdict]:
        requests = [build_request(self.model, question) for question in questions]
        keys = [self._key(request) for request in requests]
        todo = [index for index, key in enumerate(keys) if key not in self._cache]
        fresh: dict[int, JudgeVerdict] = {}
        rejected: JudgeError | None = None
        try:
            with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
                futures = {index: pool.submit(self._ask, requests[index]) for index in todo}
                # Collect every answer before raising a rejection, so requests that did succeed are cached.
                for index, future in futures.items():
                    try:
                        verdict = future.result()
                    except JudgeError as exc:
                        rejected = rejected or exc
                        continue
                    fresh[index] = verdict
                    if verdict.error is None:
                        self._cache[keys[index]] = asdict(verdict)
        finally:
            # Keep whatever was paid for, even if a rejected request stops the run.
            if fresh:
                self._save_cache()
        if rejected:
            raise rejected
        return [fresh[index] if index in fresh else JudgeVerdict(**self._cache[keys[index]])
                for index in range(len(questions))]
