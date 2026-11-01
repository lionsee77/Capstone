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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from ..audit import ChangeEvent, LinkedReview, log_changes
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
            proposal = proposals.get(decision.proposal_id)
            if proposal is None:
                raise ValueError(
                    f"Cannot record review decision: proposal {decision.proposal_id!r} is missing from "
                    f"screening evidence for baseline {resolution.baseline_snapshot_id!r}. "
                    "Rebuild the merge resolution with matching proposals and decisions."
                )
            target = str(decision.existing_concept_id) if decision.existing_concept_id is not None else None
            subjects.append((proposal.level, target, f"proposal:{proposal.proposal_id}", proposal.name,
                             [proposal.proposal_id]))
        else:
            for member in decision.members:
                tag = tags.get((member.source_dataset, member.tag_id))
                if tag is None:
                    raise ValueError(
                        f"Cannot record review decision: source tag {member.source_dataset!r}/{member.tag_id!r} "
                        f"is missing from resolution evidence for baseline {resolution.baseline_snapshot_id!r}. "
                        "Rebuild the merge resolution with matching source tags and decisions."
                    )
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


@dataclass
class _IntegrationIndex:
    """Operation-local lookups keep event construction proportional to relevant relationships."""

    paths: dict[tuple[str, int | str], set[tuple[str, ...]]] = field(default_factory=dict)
    tag_specialities: dict[int | str, set[int | str]] = field(default_factory=dict)
    tag_sources: dict[int | str, set[tuple[str, str]]] = field(default_factory=dict)
    proposals_by_path: dict[tuple[str, ...], set[str]] = field(default_factory=dict)
    sources_by_path: dict[tuple[str, tuple[str, ...]], set[tuple[str, str]]] = field(default_factory=dict)
    proposals_by_source_path: dict[tuple[tuple[str, str], tuple[str, ...]], set[str]] = field(default_factory=dict)
    decisions_by_source: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    decisions_by_proposal: dict[str, set[int]] = field(default_factory=dict)
    decision_refs: list[str] = field(default_factory=list)


# Index each source collection once, preserving decision positions for deterministic output.
def _integration_index(resolution: ResolutionReport, id_map: dict[str, int]) -> _IntegrationIndex:
    index = _IntegrationIndex()
    for node in resolution.hierarchy:
        key = (node.level, id_map.get(str(node.id), node.id))
        index.paths.setdefault(key, set()).add(tuple(node.path))
    for link in resolution.links:
        tag_id = id_map.get(str(link.tag_id), link.tag_id)
        speciality_id = id_map.get(str(link.speciality_id), link.speciality_id)
        index.tag_specialities.setdefault(tag_id, set()).add(speciality_id)
    for tag in resolution.tags:
        index.tag_sources[id_map.get(str(tag.id), tag.id)] = {
            (member.source_dataset, member.tag_id) for member in tag.members
        }
    for proposal in resolution.screening.proposals if resolution.screening else []:
        path = tuple(proposal.path)
        index.proposals_by_path.setdefault(path, set()).add(proposal.proposal_id)
        index.sources_by_path.setdefault((proposal.level, path), set()).update(proposal.source_tags)
        for source in proposal.source_tags:
            index.proposals_by_source_path.setdefault((source, path), set()).add(proposal.proposal_id)
    for position, decision in enumerate(resolution.decisions):
        index.decision_refs.append(decision_reference(resolution.baseline_snapshot_id, decision))
        if decision.action not in {"create", "reuse"}:
            continue
        if decision.proposal_id:
            index.decisions_by_proposal.setdefault(decision.proposal_id, set()).add(position)
        for member in decision.members:
            source = (member.source_dataset, member.tag_id)
            index.decisions_by_source.setdefault(source, set()).add(position)
    return index


# Resolve approved paths through allocated IDs, retaining only the current link's speciality when appropriate.
def _related_paths(index: _IntegrationIndex, level: str, row: dict[str, Any]) -> set[tuple[str, ...]]:
    if level in {"category", "speciality"}:
        paths = index.paths.get((level, row["id"]), set())
    else:
        speciality_ids = ({row["speciality_id"]} if level == "speciality_tag"
                          else index.tag_specialities.get(row["id"], set()))
        paths = {path for speciality_id in speciality_ids
                 for path in index.paths.get(("speciality", speciality_id), set())}
    return paths | {path[:2] for path in paths}


# Build snapshot additions with concrete IDs and all applicable proposal and decision references.
def integration_events(
    resolution: ResolutionReport, additions: dict[str, list[dict[str, Any]]], id_map: dict[str, int],
    manifest: dict[str, Any], *, operation_id: str, actor: str,
    review_events: Sequence[ChangeEvent] = (),
) -> list[ChangeEvent]:
    index = _integration_index(resolution, id_map)
    reviews = linked_review_evidence(resolution, review_events)
    events = []
    for table, rows in additions.items():
        level = {"categories": "category", "specialities": "speciality", "tags": "tag",
                 "speciality_tags": "speciality_tag"}[table]
        for row in rows:
            paths = _related_paths(index, level, row)
            if level in {"category", "speciality"}:
                proposal_ids = {pid for path in paths for pid in index.proposals_by_path.get(path, ())}
                sources = {source for path in paths for source in index.sources_by_path.get((level, path), ())}
            else:
                tag_id = row["tag_id"] if level == "speciality_tag" else row["id"]
                sources = index.tag_sources[tag_id]
                proposal_ids = {pid for path in paths for source in sources
                                for pid in index.proposals_by_source_path.get((source, path), ())}
            positions = set()
            for proposal_id in proposal_ids:
                positions.update(index.decisions_by_proposal.get(proposal_id, ()))
            for source in sources:
                positions.update(index.decisions_by_source.get(source, ()))
            ordered = sorted(positions)
            decisions = [resolution.decisions[position] for position in ordered]
            references = [index.decision_refs[position] for position in ordered]
            name = str(row.get("name", f"speciality:{row.get('speciality_id')} -> tag:{row.get('tag_id')}"))
            events.append(ChangeEvent(
                event_id=f"{operation_id}:{table}:{row['id']}", operation_id=operation_id,
                dataset=manifest["baseline"]["dataset"], snapshot_id=manifest["enriched_snapshot_id"],
                concept_level=level, concept_id=str(row["id"]), concept_ref=f"{level}:{row['id']}",
                concept_name=name, action="add", after=row,
                reason="; ".join(dict.fromkeys(d.reason for d in decisions)) or "Accepted by enrichment rules",
                actor=actor, occurred_at=datetime.fromisoformat(manifest["created_at"]),
                proposal_ids=sorted(proposal_ids),
                decision_ids=references,
                linked_reviews=[review for reference in references for review in reviews[reference]],
                scope="snapshot",
            ))
    return events


# Group original submissions by decision while preserving unknown attribution for legacy artifacts.
def linked_review_evidence(
    resolution: ResolutionReport, review_events: Sequence[ChangeEvent],
) -> dict[str, list[LinkedReview]]:
    submitted: dict[str, dict[str, list[ChangeEvent]]] = {}
    for event in review_events:
        if (event.action != "review_decision" or event.dataset != resolution.evidence.taxonomy
                or event.snapshot_id != resolution.baseline_snapshot_id):
            continue
        for reference in event.decision_ids:
            submitted.setdefault(reference, {}).setdefault(event.operation_id, []).append(event)
    result = {}
    for decision in resolution.decisions:
        reference = decision_reference(resolution.baseline_snapshot_id, decision)
        payload = decision.model_dump(mode="json")
        reviews = []
        for entries in submitted.get(reference, {}).values():
            if any(entry.decision != payload for entry in entries):
                raise ValueError(f"Saved review evidence conflicts with decision {reference}")
            first = entries[0]
            if any((entry.actor, entry.occurred_at) != (first.actor, first.occurred_at) for entry in entries):
                raise ValueError(f"Saved review attribution conflicts for decision {reference}")
            reviews.append(LinkedReview(
                decision_id=reference, decision=payload, actor=first.actor, occurred_at=first.occurred_at,
                review_event_ids=sorted({entry.event_id for entry in entries}),
            ))
        result[reference] = reviews or [LinkedReview(decision_id=reference, decision=payload)]
    return result
