"""Review submissions append local PB09 events without inventing another decision format."""
from __future__ import annotations

from pathlib import Path

from taxonomy_pipeline.classification.change_log import append_review_decisions
from taxonomy_pipeline.classification.models import ReviewDecision, ReviewDecisionEvent, SourceTagRef


# Retain reasons, targets, timestamps and independent submissions for both kinds of review decision.
def test_change_log_appends_tag_and_hierarchy_decisions_without_overwriting(tmp_path: Path) -> None:
    path = tmp_path / "change_log.jsonl"
    choices = [
        ReviewDecision(members=[SourceTagRef(source_dataset="feed", tag_id="1")],
                       action="exclude", reason="Out of scope"),
        ReviewDecision(proposal_id="proposal", action="reuse", existing_concept_id=42, reason="Same concept"),
    ]
    append_review_decisions(path, choices, baseline_snapshot_id="snapshot")
    first = path.read_bytes()
    append_review_decisions(path, [choices[1]], baseline_snapshot_id="snapshot")
    assert path.read_bytes().startswith(first)
    events = [ReviewDecisionEvent.model_validate_json(line) for line in path.read_text().splitlines()]
    assert [event.decision for event in events] == [*choices, choices[1]]
    assert len({event.event_id for event in events}) == 3
    assert events[0].submission_id == events[1].submission_id != events[2].submission_id
    assert all(event.recorded_at.utcoffset().total_seconds() == 0 for event in events)
    assert all(event.baseline_snapshot_id == "snapshot" for event in events)


# Screening without a reviewer submission must not create misleading decision events.
def test_no_review_submission_does_not_create_a_change_log(tmp_path: Path) -> None:
    path = tmp_path / "change_log.jsonl"
    append_review_decisions(path, [], baseline_snapshot_id="snapshot")
    assert not path.exists()
