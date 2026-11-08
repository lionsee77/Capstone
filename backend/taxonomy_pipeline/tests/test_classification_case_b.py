"""Case B: strict input selection, proposal checks, and a full run against a stub model."""
from __future__ import annotations

import asyncio
import csv
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from taxonomy_pipeline.enrichment import case_b
from taxonomy_pipeline.enrichment.case_b import (
    CaseBError,
    baseline_hierarchy,
    classify_batch,
    proposal_is_well_formed,
    run_case_b,
    to_contract_records,
)
from taxonomy_pipeline.enrichment.models import CLASSIFICATION_OUTPUTS
from taxonomy_pipeline.providers.tag_context import TagContextProvider
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

PROPOSAL = {
    "tag": "Skill", "tag_id": "3001", "action": "assign_existing", "service": "Industry", "category": "Banking",
    "speciality": "Retail Banking", "confidence": 0.8, "reason": "Matches the existing speciality.",
}
CONCEPTS = [
    {"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [3001, 3002]},
    {"recommended_action": "ADD_CANDIDATE", "parent_status": "HAS_PARENT", "incoming_ids": [3003]},
]


def _datasets() -> dict[str, TaxonomyDataset]:
    return {
        "original": TaxonomyDataset(
            name="original",
            services=[{"id": 1, "name": "Industry"}, {"id": 3, "name": "Subject"}],
            categories=[{"id": 1, "name": "Banking", "service_id": 1}, {"id": 2, "name": "Arts", "service_id": 3}],
            specialities=[{"id": 1, "name": "Retail Banking", "category_id": 1},
                          {"id": 2, "name": "Orphan", "category_id": 99}],
        ),
        "skillsfuture": TaxonomyDataset(
            name="skillsfuture",
            tags=[{"id": 3001, "name": "Loan Processing"}, {"id": 3002, "name": "Stage Lighting"},
                  {"id": 3003, "name": "Has A Parent"}],
        ),
    }


class StubClient:
    """Answers like the OpenAI client; each call pops the next scripted response body."""

    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model, messages, temperature):
        self.prompts.append(messages[0]["content"])
        content = self.bodies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _response(*proposals):
    return json.dumps({"proposals": list(proposals)})


@pytest.fixture
def workbook(tmp_path):
    """Generated SkillsFuture context; fixture name retained for existing run helpers."""
    path = tmp_path / "skillsfuture_tag_context.json"
    path.write_text(json.dumps({
        "source": "skillsfuture", "display_name": "SkillsFuture", "tags": {
            "3000": {"skill_id": "SK-A", "title": "Unrelated Skill", "description": "Not a Case B tag"},
            "3001": {"skill_id": "SK-B", "title": "Loan Processing", "description": "Processes loans"},
        },
    }), encoding="utf-8")
    return path


def test_proposal_check_rejects_invalid_actions_and_confidence():
    assert proposal_is_well_formed(PROPOSAL)
    assert not proposal_is_well_formed({**PROPOSAL, "action": "unknown"})
    assert not proposal_is_well_formed({**PROPOSAL, "confidence": 80})
    assert not proposal_is_well_formed({key: value for key, value in PROPOSAL.items() if key != "reason"})


def test_contract_preserves_model_selected_service():
    proposal = {**PROPOSAL, "service": "Subject", "category": "Arts", "speciality": "Stage"}

    records = to_contract_records([proposal], {"3001": "Skill"}, source_dataset="skillsfuture", run_id=None)

    assert records[0]["service"] == "Subject"


def test_baseline_hierarchy_names_each_speciality_by_its_category():
    services, categories, specialities = baseline_hierarchy(_datasets()["original"])

    assert services == ["Industry", "Subject"]
    assert categories == [
        {"service": "Industry", "name": "Banking"},
        {"service": "Subject", "name": "Arts"},
    ]
    assert specialities == [
        {"service": "Industry", "category": "Banking", "name": "Retail Banking"},
        {"service": "", "category": "99", "name": "Orphan"},
    ]


def test_a_specialitys_category_and_service_come_from_the_same_category_row():
    baseline = TaxonomyDataset(
        name="dup", services=[{"id": 1, "name": "Industry"}, {"id": 2, "name": "Subject"}],
        categories=[{"id": 5, "name": "First", "service_id": 1}, {"id": 5, "name": "Second", "service_id": 2}],
        specialities=[{"id": 9, "name": "Leaf", "category_id": 5}],
    )
    _, _, specialities = baseline_hierarchy(baseline)
    assert specialities == [{"service": "Subject", "category": "Second", "name": "Leaf"}]


def test_classify_batch_retries_until_every_tag_has_a_proposal():
    client = StubClient("not json", _response(PROPOSAL), _response({**PROPOSAL, "tag_id": "3002"}))
    batch = [{"tag_id": "3001"}, {"tag_id": "3002"}]

    proposals = asyncio.run(classify_batch(client, "prompt", batch, "model"))

    assert [proposal["tag_id"] for proposal in proposals] == ["3001", "3002"]
    assert "missing tag_id values: ['3002']" in client.prompts[-1]


def test_classify_batch_gives_up_after_three_attempts():
    with pytest.raises(CaseBError, match="failed to return one valid proposal"):
        asyncio.run(classify_batch(StubClient("{}", "{}", "{}"), "prompt", [{"tag_id": "3001"}], "model"))


def test_run_classifies_only_case_b_tags_and_writes_contract_records(tmp_path, workbook, saturation_report):
    unresolved = {**PROPOSAL, "tag_id": "3002", "action": "propose_new_category", "category": "", "speciality": ""}
    client = StubClient(_response(PROPOSAL, unresolved))
    output = tmp_path / "out" / "case_b_output.json"

    records = asyncio.run(run_case_b(
        _datasets(), saturation_report(CONCEPTS, run_id="sat-9"), baseline="original", incoming="skillsfuture",
        provider=TagContextProvider(), context=workbook, output=output, client=client,
    ))

    assert [record["tag_id"] for record in records] == ["3001", "3002"]
    assert records[0] == {
        "action": "assign_existing", "source_dataset": "skillsfuture", "tag_id": "3001", "tag": "Loan Processing",
        "original_classification": "unclassified", "service": "Industry", "category": "Banking",
        "speciality": "Retail Banking", "confidence": 0.8, "reason": "Matches the existing speciality.",
        "saturation_run_id": "sat-9",
    }
    assert records[1]["category"] is records[1]["service"] is records[1]["confidence"] is None
    CLASSIFICATION_OUTPUTS.validate_json(output.read_text(encoding="utf-8"))
    assert output.with_suffix(".csv").exists()
    assert not output.with_name(".case_b_output.checkpoint.json").exists()
    # Like the generator, tag IDs are 3000 + position in the sorted skill_ids, so 3001 is SK-B.
    assert '"skill_id": "SK-B"' in client.prompts[0] and "Processes loans" in client.prompts[0]
    assert "Has A Parent" not in client.prompts[0]
    assert '"Industry"' in client.prompts[0] and "Allowed services" in client.prompts[0]


def _six_tag_datasets() -> dict[str, TaxonomyDataset]:
    datasets = _datasets()
    datasets["skillsfuture"] = TaxonomyDataset(
        name="skillsfuture", tags=[{"id": 3000 + n, "name": f"Skill {n}"} for n in range(1, 7)],
    )
    return datasets


SIX_TAGS = [{"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT",
             "incoming_ids": list(range(3001, 3007))}]


def _answers(*tag_ids):
    return _response(*({**PROPOSAL, "tag_id": str(tag_id)} for tag_id in tag_ids))


def _run(report, output, client, **kwargs):
    return asyncio.run(run_case_b(
        _six_tag_datasets(), report, baseline="original", incoming="skillsfuture",
        provider=TagContextProvider(), context=kwargs.pop("workbook"), output=output, client=client, **kwargs,
    ))


class CrashAfter(StubClient):
    """Answers the scripted batches, then fails every call like a dropped connection."""

    async def _create(self, *, model, messages, temperature):
        if not self.bodies:
            self.prompts.append(messages[0]["content"])
            raise ConnectionError("network down")
        return await super()._create(model=model, messages=messages, temperature=temperature)


def test_run_accepts_a_registered_provider_by_name(tmp_path, workbook, saturation_report):
    records = asyncio.run(run_case_b(
        _datasets(), saturation_report(CONCEPTS), baseline="original", incoming="skillsfuture",
        provider="tag_context", context=workbook, output=tmp_path / "out.json",
        client=StubClient(_response(PROPOSAL, {**PROPOSAL, "tag_id": "3002"})),
    ))
    assert [record["tag_id"] for record in records] == ["3001", "3002"]


def test_run_names_an_imported_source_and_adds_its_descriptions_to_the_prompt(
    tmp_path: Path, saturation_report: Callable[..., Path],
) -> None:
    context = tmp_path / "esco_tag_context.json"
    context.write_text(json.dumps({"display_name": "ESCO", "tags": {"3001": {"description": "Approves loans"}}}),
                       encoding="utf-8")
    client = StubClient(_response(PROPOSAL, {**PROPOSAL, "tag_id": "3002"}))
    asyncio.run(run_case_b(
        _datasets(), saturation_report(CONCEPTS), baseline="original", incoming="skillsfuture",
        provider="tag_context", context=context, output=tmp_path / "out.json", client=client,
    ))
    assert "incoming ESCO skills" in client.prompts[0] and "Approves loans" in client.prompts[0]


def test_run_refuses_an_unregistered_provider_name(tmp_path, workbook, saturation_report):
    with pytest.raises(ValueError, match="No source provider 'nope'"):
        asyncio.run(run_case_b(
            _datasets(), saturation_report(CONCEPTS), baseline="original", incoming="skillsfuture",
            provider="nope", context=workbook, output=tmp_path / "out.json", client=StubClient(),
        ))


def test_run_logs_its_progress_instead_of_printing_it(tmp_path, workbook, saturation_report, caplog, capsys):
    unresolved = {**PROPOSAL, "tag_id": "3002", "action": "propose_new_category", "category": "", "speciality": ""}
    with caplog.at_level(logging.DEBUG, logger="taxonomy_pipeline.enrichment.case_b"):
        _run(saturation_report(CONCEPTS), tmp_path / "out.json", StubClient(_response(PROPOSAL, unresolved)),
             workbook=workbook)

    messages = [record.getMessage() for record in caplog.records]
    assert messages[0].startswith("Using 2 Case B tag(s) from")
    assert "Processed 2/2" in messages and messages[-1].startswith("Wrote 2 classifications to")
    assert any(record.levelno == logging.DEBUG and "Raw model response" in record.getMessage()
               for record in caplog.records)
    assert capsys.readouterr().out == ""


def test_run_resumes_from_a_checkpoint_without_calling_the_model_again(tmp_path, workbook, saturation_report):
    report, output = saturation_report(SIX_TAGS), tmp_path / "case_b_output.json"
    with pytest.raises(CaseBError):
        _run(report, output, CrashAfter(_answers(3001, 3002, 3003)), workbook=workbook, batch_size=3)
    assert output.with_name(".case_b_output.checkpoint.json").exists()

    client = StubClient(_answers(3004, 3005, 3006))
    records = _run(report, output, client, workbook=workbook, batch_size=3)

    assert [record["tag_id"] for record in records] == [str(n) for n in range(3001, 3007)]
    assert len(client.prompts) == 1
    assert not output.with_name(".case_b_output.checkpoint.json").exists()


def test_resuming_with_a_different_batch_size_does_not_duplicate_records(tmp_path, workbook, saturation_report):
    report, output = saturation_report(SIX_TAGS), tmp_path / "case_b_output.json"
    with pytest.raises(CaseBError):
        _run(report, output, CrashAfter(_answers(3001, 3002, 3003, 3004)), workbook=workbook, batch_size=4)

    client = StubClient(_answers(3005, 3006))
    records = _run(report, output, client, workbook=workbook, batch_size=3)

    assert [record["tag_id"] for record in records] == [str(n) for n in range(3001, 3007)]
    assert len(client.prompts) == 1
    assert '"tag_id": "3004"' not in client.prompts[0] and '"tag_id": "3005"' in client.prompts[0]


def test_refuses_a_checkpoint_from_a_different_saturation_run(tmp_path, workbook, saturation_report):
    output = tmp_path / "case_b_output.json"
    with pytest.raises(CaseBError):
        _run(saturation_report(SIX_TAGS, run_id="run-1"), output, CrashAfter(_answers(3001, 3002, 3003)),
             workbook=workbook, batch_size=3)

    client = StubClient(_answers(3001, 3002, 3003, 3004, 3005, 3006))
    with pytest.raises(CaseBError, match="different saturation run"):
        _run(saturation_report(SIX_TAGS, run_id="run-2", name="new.json"), output, client, workbook=workbook)
    assert client.prompts == []


def test_refuses_a_checkpoint_written_by_a_different_model(tmp_path, workbook, saturation_report):
    report, output = saturation_report(SIX_TAGS), tmp_path / "case_b_output.json"
    with pytest.raises(CaseBError):
        _run(report, output, CrashAfter(_answers(3001, 3002, 3003)), workbook=workbook, batch_size=3, model="m1")

    with pytest.raises(CaseBError, match="model 'm1'"):
        _run(report, output, StubClient(), workbook=workbook, model="m2")


def test_refuses_a_checkpoint_in_the_old_list_format(tmp_path, workbook, saturation_report):
    output = tmp_path / "case_b_output.json"
    output.with_name(".case_b_output.checkpoint.json").write_text(json.dumps([PROPOSAL]), encoding="utf-8")

    with pytest.raises(CaseBError, match="no record of its saturation run"):
        _run(saturation_report(SIX_TAGS), output, StubClient(), workbook=workbook)


def test_refuses_a_checkpoint_without_service_before_calling_the_model(tmp_path, workbook, saturation_report):
    output = tmp_path / "case_b_output.json"
    checkpoint = output.with_name(".case_b_output.checkpoint.json")
    checkpoint.write_text(json.dumps({
        "saturation_run_id": "run-1", "incoming": "skillsfuture", "model": "gpt-4o-mini",
        "context_schema_version": case_b.CONTEXT_SCHEMA_VERSION,
        "proposals": [{key: value for key, value in PROPOSAL.items() if key != "service"}],
    }), encoding="utf-8")
    client = StubClient()

    with pytest.raises(CaseBError, match="incompatible format"):
        _run(saturation_report(SIX_TAGS), output, client, workbook=workbook)
    assert client.prompts == []


def test_run_rejects_report_tags_missing_from_the_incoming_dataset(tmp_path, workbook, saturation_report):
    concepts = [{"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [42]}]

    with pytest.raises(CaseBError, match="exist in dataset 'skillsfuture'"):
        asyncio.run(run_case_b(
            _datasets(), saturation_report(concepts), baseline="original", incoming="skillsfuture",
            provider=TagContextProvider(), context=workbook, output=tmp_path / "out.json", client=StubClient(),
        ))


def test_missing_llm_dependencies_explain_the_extra(monkeypatch):
    import builtins

    real_import = builtins.__import__
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def without_openai(name, *args, **kwargs):
        if name == "openai":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_openai)
    with pytest.raises(CaseBError, match=r"\.\[llm\]"):
        case_b._openai_client()


def test_flags_ambiguous_tags_for_review_in_the_json_and_csv(tmp_path, workbook, saturation_report):
    concepts = [
        {"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [3001]},
        {"recommended_action": "REVIEW_AMBIGUOUS", "parent_status": "NO_PARENT", "incoming_ids": [3002],
         "reason": "partial_similarity", "closest_semantic_baseline_name": "Lighting Design",
         "closest_semantic_score": 0.61, "closest_semantic_baseline_level": "tags"},
    ]
    client = StubClient(_response(PROPOSAL, {**PROPOSAL, "tag_id": "3002", "category": "Arts", "speciality": "Stage"}))
    output = tmp_path / "case_b_output.json"

    records = asyncio.run(run_case_b(
        _datasets(), saturation_report(concepts), baseline="original", incoming="skillsfuture",
        provider=TagContextProvider(), context=workbook, output=output, client=client,
    ))

    assert "needs_review" not in records[0]
    assert records[1]["needs_review"] is True
    assert "tag 'Lighting Design' in meaning (0.61)" in records[1]["reason"]
    with output.with_suffix(".csv").open(encoding="utf-8") as stream:
        assert [row["needs_review"] for row in csv.DictReader(stream)] == ["", "True"]


RETRY_PROPOSAL = {
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
    assert client.options == {}


def test_retry_after_controls_the_delay_before_recovery(waits: list[float]) -> None:
    client = Client(RequestFailure(429, "7"), json.dumps({"proposals": [RETRY_PROPOSAL]}))
    assert asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}], "model")) == [RETRY_PROPOSAL]
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
    other = {**RETRY_PROPOSAL, "tag_id": "2"}
    client = Client(
        json.dumps({"proposals": [RETRY_PROPOSAL]}), RequestFailure(503), json.dumps({"proposals": [other]}),
    )
    result = asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}, {"tag_id": "2"}], "model"))
    assert result == [RETRY_PROPOSAL, other]


def test_malformed_json_can_recover(waits: list[float]) -> None:
    client = Client("[]", "not JSON", json.dumps({"proposals": [RETRY_PROPOSAL]}))
    assert asyncio.run(case_b.classify_batch(client, "prompt", [{"tag_id": "1"}], "model")) == [RETRY_PROPOSAL]


def test_old_context_checkpoint_is_rejected_before_model_calls(tmp_path, workbook, saturation_report):
    output = tmp_path / "out.json"
    checkpoint = output.with_name(".out.checkpoint.json")
    checkpoint.write_text(json.dumps({
        "saturation_run_id": "run-1", "incoming": "skillsfuture", "model": "test-model",
        "proposals": [PROPOSAL],
    }), encoding="utf-8")
    client = StubClient()
    with pytest.raises(CaseBError, match="context_schema_version"):
        _run(saturation_report(CONCEPTS), output, client, workbook=workbook, model="test-model")
    assert client.prompts == []


def test_retry_after_milliseconds_takes_precedence(waits: list[float]) -> None:
    error = RequestFailure(429, "1")
    error.response.headers["retry-after-ms"] = "8000"
    client = Client(error, json.dumps({"proposals": [RETRY_PROPOSAL]}))
    asyncio.run(classify_batch(client, "prompt", [{"tag_id": "1"}], "model"))
    assert waits == [8]


@pytest.mark.parametrize("value", ["inf", "1e999"])
def test_infinite_retry_after_stops_the_run(value: str, waits: list[float]) -> None:
    client = Client(RequestFailure(429, value))
    with pytest.raises(CaseBError, match="above 60 seconds"):
        asyncio.run(classify_batch(client, "prompt", [{"tag_id": "1"}], "model"))
    assert client.calls == 1
    assert waits == []


def test_server_retry_false_prevents_retry(waits: list[float]) -> None:
    error = RequestFailure(503)
    error.response.headers["x-should-retry"] = "false"
    client = Client(error)
    with pytest.raises(RequestFailure):
        asyncio.run(classify_batch(client, "prompt", [{"tag_id": "1"}], "model"))
    assert client.calls == 1
    assert waits == []


def test_programming_errors_keep_the_original_message(waits: list[float]) -> None:
    client = Client(TypeError("unexpected argument"))
    with pytest.raises(TypeError, match="unexpected argument"):
        asyncio.run(classify_batch(client, "prompt", [{"tag_id": "1"}], "model"))
    assert client.calls == 1


def test_injected_mock_client_is_not_replaced():
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"proposals": [RETRY_PROPOSAL]})))],
    ))
    assert asyncio.run(classify_batch(client, "prompt", [{"tag_id": "1"}], "model")) == [RETRY_PROPOSAL]
    client.with_options.assert_not_called()


def test_server_can_request_a_retry_for_an_otherwise_unclassified_status(waits: list[float]) -> None:
    error = RequestFailure(418)
    error.response.headers["x-should-retry"] = "true"
    client = Client(error, json.dumps({"proposals": [RETRY_PROPOSAL]}))
    asyncio.run(classify_batch(client, "prompt", [{"tag_id": "1"}], "model"))
    assert waits == [1]


def test_sdk_response_validation_error_can_recover(waits: list[float]) -> None:
    sdk = pytest.importorskip("openai")
    import httpx

    response = httpx.Response(200, request=httpx.Request("POST", "https://example.test"))
    client = Client(
        sdk.APIResponseValidationError(response=response, body={}), json.dumps({"proposals": [RETRY_PROPOSAL]}),
    )
    assert asyncio.run(classify_batch(client, "prompt", [{"tag_id": "1"}], "model")) == [RETRY_PROPOSAL]
    assert waits == [1]
