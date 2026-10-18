"""Persist review submissions without coupling pure resolution to audit storage.

The merge-run JSONL file is the local change log until PB09 provides shared
storage. Events embed the same ReviewDecision used by the resolver. Preview
and integration replay never append events; rerunning a submission does.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import ReviewDecision, ReviewDecisionEvent


# Append one event per validated reviewer decision while preserving earlier submissions.
def append_review_decisions(
    path: Path, decisions: Sequence[ReviewDecision], *, baseline_snapshot_id: str,
) -> None:
    if not decisions:
        return
    submission_id = str(uuid4())
    recorded_at = datetime.now(UTC)
    events = [ReviewDecisionEvent(
        event_id=str(uuid4()), submission_id=submission_id, recorded_at=recorded_at,
        baseline_snapshot_id=baseline_snapshot_id, decision=decision,
    ) for decision in decisions]
    with path.open("a", encoding="utf-8") as stream:
        stream.write("".join(event.model_dump_json() + "\n" for event in events))
