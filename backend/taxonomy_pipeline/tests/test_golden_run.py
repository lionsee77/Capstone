"""The golden run's stub model answers like Case B expects, and `compare` ignores only what varies by design."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from experiments.golden_run import StubModel, compare
from taxonomy_pipeline.enrichment.case_b import build_prompt, classify_batch


@pytest.mark.parametrize("source_name", ["SkillsFuture", "O*NET"])
def test_the_stub_model_answers_every_tag_in_a_real_case_b_prompt_the_same_way_each_time(source_name: str) -> None:
    services = ["Industry", "Subject"]
    categories = [{"service": "Subject", "name": "Computing"}]
    specialities = [{"service": "Subject", "category": "Computing", "name": "Data visualization"}]
    batch = [{"tag_id": str(3000 + index), "title": f"Skill {index}"} for index in range(40)]
    prompt = build_prompt(services, categories, specialities, batch, source_name=source_name)

    first = asyncio.run(classify_batch(StubModel(), prompt, batch, "stub"))
    again = asyncio.run(classify_batch(StubModel(), prompt, batch, "stub"))

    assert first == again and [proposal["tag_id"] for proposal in first] == [item["tag_id"] for item in batch]
    assert {proposal["action"] for proposal in first} == {
        "assign_existing", "propose_new_speciality", "propose_new_category",
    }
    assert any(proposal["category"] == "" for proposal in first)  # some tags get no placement


def _recording(root: Path, *, run_id: str, reason: str = "Same") -> Path:
    (root / "merge").mkdir(parents=True)
    (root / "report.json").write_text(json.dumps({
        "run_id": run_id, "created_at": run_id, "results": [{"summary": "ok", "path": f"{root.resolve()}/x.sql"}],
    }), encoding="utf-8")
    (root / "merge" / "log.jsonl").write_text(json.dumps({"event_id": run_id, "reason": reason}) + "\n",
                                              encoding="utf-8")
    return root


def test_recordings_that_differ_only_in_ids_timestamps_and_their_own_path_compare_identical(tmp_path: Path) -> None:
    assert compare(_recording(tmp_path / "a", run_id="1"), _recording(tmp_path / "b", run_id="2")) == 0


def test_a_changed_value_or_a_missing_file_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _recording(tmp_path / "a", run_id="1")
    after = _recording(tmp_path / "b", run_id="1", reason="Changed")
    (after / "report.json").unlink()

    assert compare(before, after) == 1
    out = capsys.readouterr().out
    assert "only in" in out and "report.json" in out
    assert "merge/log.jsonl: [0].reason line 1: 'Same' -> 'Changed'" in out
