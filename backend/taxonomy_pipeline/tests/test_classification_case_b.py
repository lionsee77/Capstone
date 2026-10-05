"""Case B: strict input selection, proposal checks, and a full run against a stub model."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from taxonomy_pipeline.classification import case_b
from taxonomy_pipeline.classification.case_b import (
    CaseBError,
    baseline_hierarchy,
    classify_batch,
    proposal_is_well_formed,
    run_case_b,
)
from taxonomy_pipeline.classification.models import CLASSIFICATION_OUTPUTS
from taxonomy_pipeline.datasets.base import TaxonomyDataset

PROPOSAL = {
    "tag": "Skill", "tag_id": "3001", "action": "assign_existing", "category": "Banking",
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
    book = Workbook()
    sheet = book.active
    sheet.title = "Unique Skills List"
    sheet.append(["skill_id", "skill_title", "skill_description", "skill_type"])
    sheet.append(["SK-B", "Loan Processing", "Processes loans", "Technical"])
    sheet.append(["SK-A", "Unrelated Skill", "Not a Case B tag", "Technical"])
    path = tmp_path / "unique.xlsx"
    book.save(path)
    return path


def test_proposal_check_rejects_invalid_actions_and_confidence():
    assert proposal_is_well_formed(PROPOSAL)
    assert not proposal_is_well_formed({**PROPOSAL, "action": "unknown"})
    assert not proposal_is_well_formed({**PROPOSAL, "confidence": 80})
    assert not proposal_is_well_formed({key: value for key, value in PROPOSAL.items() if key != "reason"})


def test_baseline_hierarchy_names_each_speciality_by_its_category():
    categories, specialities = baseline_hierarchy(_datasets()["original"])

    assert categories == ["Arts", "Banking"]
    assert specialities == [{"category": "Banking", "name": "Retail Banking"}, {"category": "99", "name": "Orphan"}]


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
        unique_skills=workbook, output=output, client=client,
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


def test_run_resumes_from_a_checkpoint_without_calling_the_model_again(tmp_path, workbook, saturation_report):
    output = tmp_path / "case_b_output.json"
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps([PROPOSAL, {**PROPOSAL, "tag_id": "3002"}]), encoding="utf-8")

    records = asyncio.run(run_case_b(
        _datasets(), saturation_report(CONCEPTS), baseline="original", incoming="skillsfuture",
        unique_skills=workbook, output=output, checkpoint=checkpoint, client=StubClient(),
    ))

    assert len(records) == 2
    assert not checkpoint.exists()


def test_run_rejects_report_tags_missing_from_the_incoming_dataset(tmp_path, workbook, saturation_report):
    concepts = [{"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [42]}]

    with pytest.raises(CaseBError, match="exist in dataset 'skillsfuture'"):
        asyncio.run(run_case_b(
            _datasets(), saturation_report(concepts), baseline="original", incoming="skillsfuture",
            unique_skills=workbook, output=tmp_path / "out.json", client=StubClient(),
        ))


def test_missing_llm_dependencies_explain_the_extra(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def without_openai(name, *args, **kwargs):
        if name in {"openai", "dotenv"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_openai)
    with pytest.raises(CaseBError, match=r"\.\[llm\]"):
        case_b._openai_client(debug=False)
