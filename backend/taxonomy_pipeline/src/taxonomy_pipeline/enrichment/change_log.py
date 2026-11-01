"""Persist review submissions without coupling pure resolution to audit storage.

The merge-run JSONL file remains a compatibility handoff; enrichment.audit
delivers its decisions to PB09 SQLite. Events embed the same ReviewDecision used by the resolver. Preview
and integration replay never append events. A rerun of merge-preview with the
same decisions file is a replay too: only decisions the log doesn't already
hold for that baseline are recorded, so each event is one reviewer decision.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import ReviewDecision, ReviewDecisionEvent


# Load review submissions once so deduplication and audit translation share the same evidence.
def read_review_decisions(path: Path) -> list[ReviewDecisionEvent]:
    """Return validated submissions in file order, treating a missing log as empty.

    Blank lines are ignored; invalid JSON or submission fields fail the read so
    callers cannot silently discard earlier evidence while appending or replaying.
    """
    if not path.exists():
        return []
    return [ReviewDecisionEvent.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# Append one event per newly submitted reviewer decision while preserving earlier submissions.
def append_review_decisions(
    path: Path, decisions: Sequence[ReviewDecision], *, baseline_snapshot_id: str,
    actor: str = "unspecified",
) -> tuple[int, list[ReviewDecisionEvent]]:
    """Return the appended count and all parsed entries, including earlier submissions needed for recovery.

    A decision counts as new when its content differs from every logged one, so an edited reason or action
    is logged again while an unchanged replay is not.
    """
    entries = read_review_decisions(path)
    logged = {entry.decision.model_dump_json() for entry in entries
              if entry.baseline_snapshot_id == baseline_snapshot_id}
    new = []
    for decision in decisions:
        key = decision.model_dump_json()
        if key not in logged:
            logged.add(key)
            new.append(decision)
    if not new:
        return 0, entries
    submission_id = str(uuid4())
    recorded_at = datetime.now(UTC)
    events = [ReviewDecisionEvent(
        event_id=str(uuid4()), submission_id=submission_id, recorded_at=recorded_at,
        baseline_snapshot_id=baseline_snapshot_id, decision=decision, actor=actor,
    ) for decision in new]
    with path.open("a", encoding="utf-8") as stream:
        stream.write("".join(event.model_dump_json() + "\n" for event in events))
    return len(new), [*entries, *events]
