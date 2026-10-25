"""The merge-preview runner chains every stage and writes each report for review."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from taxonomy_pipeline.enrichment.models import PlacementPath, ReviewDecision
from taxonomy_pipeline.enrichment.runner import MixedSaturationRunsError, load_decisions, run_merge_preview
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

FIXTURES = Path(__file__).parent / "fixtures" / "classification"
PRODUCER_FILES = [FIXTURES / "case_a.json", FIXTURES / "case_b.json"]


@pytest.fixture
def baseline() -> TaxonomyDataset:
    return TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))


def _stamped(tmp_path: Path, source: Path, run_id: str) -> Path:
    rows = json.loads(source.read_text(encoding="utf-8"))
    path = tmp_path / source.name
    path.write_text(json.dumps([{**row, "saturation_run_id": run_id} for row in rows]), encoding="utf-8")
    return path


def test_writes_every_stage_report_and_leaves_duplicate_candidates_pending(tmp_path, baseline):
    run = run_merge_preview(PRODUCER_FILES, baseline, out_dir=tmp_path / "run")

    assert sorted(path.name for path in (tmp_path / "run").iterdir()) == [
        "deduplication.json", "preview.csv", "resolution.json", "run.json", "validation.json",
    ]
    assert run.ok and run.preview.partial
    assert run.preview.counts.pending_sources > 0
    with (tmp_path / "run" / "preview.csv").open(encoding="utf-8-sig") as stream:
        assert any(row["status"] == "pending" for row in csv.DictReader(stream))


def review_candidate_file(tmp_path: Path) -> Path:
    """A producer file like Case A's: one tag offered under two candidate categories, flagged for review,
    next to an ordinary placement that needs nothing."""
    def record(tag_id: str, tag: str, category: str, speciality: str, **extra: object) -> dict[str, object]:
        return {
            "source_dataset": "case-a", "tag_id": tag_id, "tag": tag, "original_classification": "classified",
            "service": "Subject", "category": category, "speciality": speciality, "confidence": 0.5,
            "reason": "Proposal", **extra,
        }

    path = tmp_path / "case_a_output.json"
    path.write_text(json.dumps([
        record("9", "Fire Prevention", "Data analytics", "Fire Prevention", needs_review=True),
        record("9", "Fire Prevention", "Biology", "Fire Prevention", needs_review=True),
        record("10", "Spreadsheet Modelling", "Data analytics", "Data presentation"),
    ]), encoding="utf-8")
    return path


def approve_data_analytics() -> ReviewDecision:
    return ReviewDecision(
        members=[{"source_dataset": "case-a", "tag_id": "9"}], action="create", reason="Reviewed",
        approved_paths=[PlacementPath(service="Subject", category="Data analytics", speciality="Fire Prevention")],
    )


def _placement_rows(out_dir: Path) -> dict[tuple[str, str], dict[str, str]]:
    with (out_dir / "preview.csv").open(encoding="utf-8-sig") as stream:
        return {(row["tag"], row["category"]): row for row in csv.DictReader(stream) if row["row_type"] == "placement"}


def test_flagged_review_candidates_stay_pending_in_the_preview_until_a_decision_approves_one(tmp_path, baseline):
    inputs = [review_candidate_file(tmp_path)]

    run = run_merge_preview(inputs, baseline, out_dir=tmp_path / "unreviewed")
    rows = _placement_rows(tmp_path / "unreviewed")
    candidates = [rows[("Fire Prevention", "Data analytics")], rows[("Fire Prevention", "Biology")]]
    assert [(row["status"], row["needs_review"]) for row in candidates] == [("pending", "True")] * 2
    assert all(row["review_reason"].startswith("Producer requested review: 2 candidate placement(s)")
               for row in candidates)
    assert rows[("Spreadsheet Modelling", "Data analytics")]["status"] == "proposed"  # unflagged tags proceed
    assert run.preview.counts.pending_sources == 1

    run = run_merge_preview(inputs, baseline, out_dir=tmp_path / "reviewed", decisions=[approve_data_analytics()])
    rows = _placement_rows(tmp_path / "reviewed")
    assert rows[("Fire Prevention", "Data analytics")]["status"] == "proposed"
    assert rows[("Fire Prevention", "Biology")]["status"] == "unapproved"
    assert run.preview.counts.pending_sources == 0


def test_review_decisions_resolve_the_pending_candidates(tmp_path, baseline):
    run = run_merge_preview(
        PRODUCER_FILES, baseline, out_dir=tmp_path, decisions=load_decisions(FIXTURES / "review_decisions.json"),
    )

    assert run.ok
    assert run.preview.counts.pending_sources == 0
    assert run.preview.counts.added_tags == 3 and run.preview.counts.reused_tags == 1


def test_refuses_outputs_from_different_saturation_runs(tmp_path, baseline):
    inputs = [_stamped(tmp_path, PRODUCER_FILES[0], "run-1"), _stamped(tmp_path, PRODUCER_FILES[1], "run-2")]

    with pytest.raises(MixedSaturationRunsError, match="run-1: case_a.json; run-2: case_b.json"):
        run_merge_preview(inputs, baseline, out_dir=tmp_path / "run")
    assert not (tmp_path / "run").exists()


def test_records_the_saturation_run_and_review_state_for_integrate(tmp_path, baseline):
    inputs = [_stamped(tmp_path, path, "run-1") for path in PRODUCER_FILES]
    run = run_merge_preview(inputs, baseline, out_dir=tmp_path / "run")

    info = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert info["saturation_run_ids"] == ["run-1"]
    assert info["pending_sources"] == run.preview.counts.pending_sources > 0
    assert info["baseline_snapshot_id"] == run.preview.baseline_snapshot_id


def test_accepts_outputs_from_one_saturation_run_without_warnings(tmp_path, baseline):
    inputs = [_stamped(tmp_path, path, "run-1") for path in PRODUCER_FILES]

    run = run_merge_preview(inputs, baseline, out_dir=tmp_path / "run")

    assert run.ok and run.warnings == []


def test_warns_when_inputs_carry_no_saturation_run(tmp_path, baseline):
    run = run_merge_preview(PRODUCER_FILES, baseline, out_dir=tmp_path)

    assert [warning.split(":")[0] for warning in run.warnings] == ["case_a.json", "case_b.json"]


def test_unreadable_input_is_reported_and_fails_the_run(tmp_path, baseline):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    run = run_merge_preview([PRODUCER_FILES[0], broken], baseline, out_dir=tmp_path / "run")

    assert not run.ok and "broken.json" in run.validation.input_errors


def test_blocked_preview_fails_the_run(tmp_path, baseline):
    row = {
        "source_dataset": "feed", "tag_id": "1", "tag": "Reporting", "original_classification": "classified",
        "service": "Subject", "category": "Data analytics", "speciality": "Data presentation", "confidence": 0.8,
        "reason": "Proposal",
    }
    source = tmp_path / "feed.json"
    source.write_text(json.dumps([row]), encoding="utf-8")
    new_tag_id = run_merge_preview([source], baseline, out_dir=tmp_path / "first").preview.mappings[0].canonical_tag_id
    baseline.tags.append({"id": new_tag_id, "name": "Preexisting ID collision"})

    run = run_merge_preview([source], baseline, out_dir=tmp_path / "second")

    assert run.preview.blocked and not run.ok


def test_legacy_exports_go_through_their_adapters(tmp_path, baseline):
    run = run_merge_preview(
        [], baseline, out_dir=tmp_path, legacy_case_a=[FIXTURES / "legacy_case_a.json"],
        legacy_case_b={FIXTURES / "legacy_case_b.json": "skillsfuture"}, skip_case_a_handoffs=True,
    )

    assert run.validation.complete
    assert run.validation.counts["skipped"] == 1
    assert {output.source_dataset for output in run.validation.merged_outputs} >= {"skillsfuture"}


def test_the_same_file_cannot_be_given_twice(tmp_path, baseline):
    with pytest.raises(ValueError, match="only once"):
        run_merge_preview([PRODUCER_FILES[0]], baseline, out_dir=tmp_path, legacy_case_a=[PRODUCER_FILES[0]])
