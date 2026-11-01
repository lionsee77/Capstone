"""Translate enrichment evidence into the shared audit contract.

Review is a decision, not a taxonomy mutation. Integration events describe the
generated snapshot, never an application to the client's live MariaDB database.
Event files remain with artifacts so SQLite delivery can be retried separately.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from ..audit import ChangeEvent, log_changes
from .models import ResolutionReport, ReviewDecision, ReviewDecisionEvent

EVENTS = TypeAdapter(list[ChangeEvent])


class AuditDeliveryError(ValueError):
    """Saved evidence could not reach SQLite; callers decide whether the workflow can still succeed."""


# Deliver already-saved events with consistent recovery guidance for expected storage failures.
def deliver_saved_changes(events: list[ChangeEvent], events_path: Path) -> None:
    try:
        log_changes(events)
    except (sqlite3.Error, OSError, ValueError) as exc:
        recovery = (
            "Resolve the conflicting or invalid audit evidence before retrying"
            if isinstance(exc, ValueError)
            else "Restore database access, then retry"
        )
        raise AuditDeliveryError(
            f"Audit events saved at {events_path}, but audit delivery failed: {exc}. "
            f"{recovery} with taxonomy-pipeline sync-changes --events \"{events_path}\". "
            "Retry only audit delivery using the saved file."
        ) from exc


# Identify the same decision across its review and all snapshots produced from it.
def decision_reference(baseline: str, decision: ReviewDecision) -> str:
    payload = json.dumps([baseline, decision.model_dump(mode="json")], sort_keys=True)
    return "decision:" + hashlib.sha256(payload.encode()).hexdigest()


# Persist a complete retryable event file before delivering it to SQLite.
def record_review_changes(
    resolution: ResolutionReport, entries: Sequence[ReviewDecisionEvent], out_dir: Path,
) -> None:
    if not resolution.decisions:
        return
    proposals = {p.proposal_id: p for p in resolution.screening.proposals} if resolution.screening else {}
    tags = {(t.source.source_dataset, t.source.tag_id): t for t in resolution.evidence.tags}
    submitted = {decision_reference(resolution.baseline_snapshot_id, d) for d in resolution.decisions}
    path = out_dir / "audit_events.json"
    accumulated = {e.event_id: e for e in EVENTS.validate_json(path.read_bytes())} if path.exists() else {}
    for entry in entries:
        reference = decision_reference(entry.baseline_snapshot_id, entry.decision)
        if entry.baseline_snapshot_id != resolution.baseline_snapshot_id or reference not in submitted:
            continue
        decision = entry.decision
        subjects = []
        if decision.proposal_id:
            proposal = proposals[decision.proposal_id]
            target = str(decision.existing_concept_id) if decision.existing_concept_id is not None else None
            subjects.append((proposal.level, target, f"proposal:{proposal.proposal_id}", proposal.name,
                             [proposal.proposal_id]))
        else:
            for member in decision.members:
                tag = tags[(member.source_dataset, member.tag_id)]
                target = str(decision.existing_tag_id) if decision.existing_tag_id is not None else None
                source_ref = "source:" + json.dumps([member.source_dataset, member.tag_id], separators=(",", ":"))
                subjects.append(("tag", target, source_ref, tag.tag, []))
        for level, concept_id, concept_ref, name, proposal_ids in subjects:
            event_id = entry.event_id + ":" + hashlib.sha256(concept_ref.encode()).hexdigest()[:16]
            event = ChangeEvent(
                event_id=event_id, operation_id=entry.submission_id, dataset=resolution.evidence.taxonomy,
                snapshot_id=resolution.baseline_snapshot_id, concept_level=level, concept_id=concept_id,
                concept_ref=concept_ref, concept_name=name, action="review_decision", reason=decision.reason,
                actor=entry.actor, occurred_at=entry.recorded_at, proposal_ids=proposal_ids,
                decision_ids=[reference], decision=decision.model_dump(mode="json"), scope="review",
            )
            if event_id in accumulated and accumulated[event_id] != event:
                raise ValueError(f"Conflicting saved review audit event: {event_id}")
            accumulated[event_id] = event
    events = list(accumulated.values())
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(EVENTS.dump_json(events, indent=2))
    temporary.replace(path)
    deliver_saved_changes(events, path)


# Find only approved hierarchy paths involved in a row, including each speciality's parent proposal.
def _related_paths(
    resolution: ResolutionReport, level: str, row: dict[str, Any], id_map: dict[str, int],
) -> set[tuple[str, ...]]:
    if level in {"category", "speciality"}:
        paths = {tuple(n.path) for n in resolution.hierarchy
                 if n.level == level and id_map.get(str(n.id), n.id) == row["id"]}
    else:
        tag_id = row["tag_id"] if level == "speciality_tag" else row["id"]
        speciality_ids = {id_map.get(str(link.speciality_id), link.speciality_id) for link in resolution.links
                          if id_map.get(str(link.tag_id), link.tag_id) == tag_id}
        if level == "speciality_tag":
            speciality_ids &= {row["speciality_id"]}
        paths = {tuple(n.path) for n in resolution.hierarchy
                 if n.level == "speciality" and id_map.get(str(n.id), n.id) in speciality_ids}
    return paths | {path[:2] for path in paths}


# Build snapshot additions with concrete IDs and all applicable proposal and decision references.
def integration_events(
    resolution: ResolutionReport, additions: dict[str, list[dict[str, Any]]], id_map: dict[str, int],
    manifest: dict[str, Any], *, operation_id: str, actor: str,
) -> list[ChangeEvent]:
    proposals = resolution.screening.proposals if resolution.screening else []
    events = []
    for table, rows in additions.items():
        level = {"categories": "category", "specialities": "speciality", "tags": "tag",
                 "speciality_tags": "speciality_tag"}[table]
        for row in rows:
            sources: set[tuple[str, str]] = set()
            paths = _related_paths(resolution, level, row, id_map)
            related = [p for p in proposals if tuple(p.path) in paths]
            proposal_ids = {p.proposal_id for p in related}
            if level in {"category", "speciality"}:
                for proposal in related:
                    if proposal.level == level:
                        sources.update(proposal.source_tags)
            else:
                tag_id = row["tag_id"] if level == "speciality_tag" else row["id"]
                tag = next(t for t in resolution.tags if id_map.get(str(t.id), t.id) == tag_id)
                sources = {(m.source_dataset, m.tag_id) for m in tag.members}
                proposal_ids = {p.proposal_id for p in related if sources.intersection(p.source_tags)}
            decisions = [d for d in resolution.decisions if d.action in {"create", "reuse"}
                         and (d.proposal_id in proposal_ids
                              or any((m.source_dataset, m.tag_id) in sources for m in d.members))]
            name = str(row.get("name", f"speciality:{row.get('speciality_id')} -> tag:{row.get('tag_id')}"))
            events.append(ChangeEvent(
                event_id=f"{operation_id}:{table}:{row['id']}", operation_id=operation_id,
                dataset=manifest["baseline"]["dataset"], snapshot_id=manifest["enriched_snapshot_id"],
                concept_level=level, concept_id=str(row["id"]), concept_ref=f"{level}:{row['id']}",
                concept_name=name, action="add", after=row,
                reason="; ".join(dict.fromkeys(d.reason for d in decisions)) or "Accepted by enrichment rules",
                actor=actor, occurred_at=datetime.fromisoformat(manifest["created_at"]),
                proposal_ids=sorted(proposal_ids),
                decision_ids=[decision_reference(resolution.baseline_snapshot_id, d) for d in decisions],
                scope="snapshot",
            ))
    return events
