"""Review evidence is durable, retryable, and distinct from snapshot changes."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from taxonomy_pipeline.app.changes import sync_changes
from taxonomy_pipeline.audit import ChangeEvent, ChangeQuery, read_changes
from taxonomy_pipeline.enrichment import audit
from taxonomy_pipeline.enrichment.audit import AuditDeliveryError, record_review_changes
from taxonomy_pipeline.enrichment.change_log import read_review_decisions
from taxonomy_pipeline.enrichment.models import CanonicalTag, ResolvedHierarchyNode, ResolvedLink, ReviewDecision
from taxonomy_pipeline.enrichment.proposals.models import ScreenedProposal
from taxonomy_pipeline.enrichment.runner import load_merge_run, run_merge_preview
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset


# Replaying a saved review retains the original actor, timestamp and decision reference without additions.
def test_review_retry_keeps_original_event_and_never_logs_additions(saved_proposal_run: Path) -> None:
    resolution, _ = load_merge_run(saved_proposal_run)
    before = read_changes(ChangeQuery())
    entries = read_review_decisions(saved_proposal_run / "change_log.jsonl")
    record_review_changes(resolution, entries, saved_proposal_run)
    assert read_changes(ChangeQuery()) == before
    assert before.total == 1
    event = before.changes[0]
    assert event.action == "review_decision" and event.scope == "review"
    assert event.concept_level == "speciality" and event.concept_id == "20"
    assert event.proposal_ids == [resolution.decisions[0].proposal_id]
    assert event.decision_ids and event.decision["reason"] == resolution.decisions[0].reason
    assert sync_changes(saved_proposal_run / "audit_events.json") == 0


# One shared-log parse supplies both replay deduplication and the original audit evidence.
def test_review_replay_reads_the_shared_log_once(
    saved_proposal_run: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = saved_proposal_run.parents[1]
    baseline = TaxonomyDataset.model_validate_json((root / "baseline.json").read_text(encoding="utf-8"))
    resolution, _ = load_merge_run(saved_proposal_run)
    before = read_changes(ChangeQuery())
    log_path = saved_proposal_run / "change_log.jsonl"
    original = log_path.read_bytes()
    read_text = Path.read_text
    reads = []

    # Count reads of only the shared decision log while allowing normal artifact reads.
    def count_log_reads(path: Path, *args: object, **kwargs: object) -> str:
        if path == log_path:
            reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", count_log_reads)
    run_merge_preview([root / "producer.json"], baseline, out_dir=saved_proposal_run,
                      decisions=resolution.decisions, actor="different caller")
    assert reads == [log_path]
    assert log_path.read_bytes() == original
    assert read_changes(ChangeQuery()) == before


@pytest.mark.parametrize("delivery_error", [
    sqlite3.OperationalError("database unavailable"),
    PermissionError("database parent access denied"),
    ValueError("Conflicting audit event ID: example"),
])
# Expected delivery failures preserve evidence and explain how to recover after fixing the cause.
def test_review_database_failure_can_be_recovered_without_rerunning_classification(
    saved_proposal_run: Path, monkeypatch: pytest.MonkeyPatch, delivery_error: Exception,
) -> None:
    root = saved_proposal_run.parents[1]
    baseline = TaxonomyDataset.model_validate_json((root / "baseline.json").read_text(encoding="utf-8"))
    resolution, _ = load_merge_run(saved_proposal_run)
    changed = resolution.decisions[0].model_copy(update={"reason": "Confirmed by client"})

    # Simulate SQLite being unavailable only after the durable review artifacts have been saved.
    def unavailable(events: list[ChangeEvent]) -> int:
        raise delivery_error

    with monkeypatch.context() as patch:
        patch.setattr(audit, "log_changes", unavailable)
        with pytest.raises(AuditDeliveryError) as failure:
            run_merge_preview(
                [root / "producer.json"], baseline, out_dir=saved_proposal_run, decisions=[changed], actor="Bryan",
            )
    assert failure.value.__cause__ is delivery_error
    message = str(failure.value)
    assert str(delivery_error) in message
    assert f'sync-changes --events "{saved_proposal_run / "audit_events.json"}"' in message
    if isinstance(delivery_error, ValueError):
        assert "Resolve the conflicting or invalid audit evidence before retrying" in message
    else:
        assert "Restore database access, then retry" in message
    assert read_changes(ChangeQuery()).total == 1
    assert sync_changes(saved_proposal_run / "audit_events.json") == 1
    assert sync_changes(saved_proposal_run / "audit_events.json") == 0
    events = read_changes(ChangeQuery()).changes
    assert events[-1].actor == "Bryan" and events[-1].reason == "Confirmed by client"
    legacy = [json.loads(line) for line in (saved_proposal_run / "change_log.jsonl").read_text().splitlines()]
    assert len(legacy) == 2


# Large sparse integrations must not rescan every source collection for each added concept.
def test_large_integration_preserves_lineage_with_bounded_source_scans(saved_proposal_run: Path) -> None:
    resolution, _ = load_merge_run(saved_proposal_run)

    class CountedList(list[Any]):
        """Count collection visits so the complexity regression does not depend on machine speed."""

        visits = 0

        # Track every yielded source item across all traversals of this collection.
        def __iter__(self) -> Iterator[Any]:
            for item in super().__iter__():
                self.visits += 1
                yield item

    count = 5000
    tags, links, nodes, proposals, decisions = (CountedList() for _ in range(5))
    id_map = {}
    additions = {"tags": [], "speciality_tags": []}
    for number in range(count):
        source = {"source_dataset": "feed", "tag_id": str(number)}
        tag_id, speciality_id = f"new:tag:{number}", f"new:speciality:{number}"
        path = ["Service", "Category", f"Speciality {number}"]
        tags.append(CanonicalTag(id=tag_id, name=f"Tag {number}", existing=False, members=[source]))
        nodes.append(ResolvedHierarchyNode(level="speciality", id=speciality_id, parent_id=1,
                                           name=path[-1], path=path, existing=False))
        links.append(ResolvedLink(speciality_id=speciality_id, tag_id=tag_id, existing=False))
        proposals.append(ScreenedProposal(proposal_id=f"p{number}", level="speciality", name=path[-1],
                                           path=path, source_tags=[("feed", str(number))], variants=[],
                                           origins=[], status="new", reason="New speciality"))
        decisions.append(ReviewDecision(members=[source], action="create", reason=f"Approved {number}"))
        id_map.update({tag_id: number + 1, speciality_id: number + 1})
        additions["tags"].append({"id": number + 1, "name": f"Tag {number}"})
        additions["speciality_tags"].append({"id": number + 1, "tag_id": number + 1,
                                              "speciality_id": number + 1})
    resolution = resolution.model_copy(update={
        "tags": tags, "links": links, "hierarchy": nodes, "decisions": decisions,
        "screening": resolution.screening.model_copy(update={"proposals": proposals}),
    })
    manifest = {"baseline": {"dataset": resolution.evidence.taxonomy}, "enriched_snapshot_id": "large",
                "created_at": "2026-11-01T00:00:00+00:00"}
    started = perf_counter()
    events = audit.integration_events(resolution, additions, id_map, manifest, operation_id="large", actor="Integrator")
    elapsed = perf_counter() - started
    assert len(events) == 2 * count
    for position, event in enumerate(events):
        number = position % count
        assert event.proposal_ids == [f"p{number}"]
        assert event.reason == f"Approved {number}"
        assert len(event.decision_ids) == len(event.linked_reviews) == 1
        assert event.linked_reviews[0].decision_id == event.decision_ids[0]
    assert all(collection.visits <= 3 * count for collection in (tags, links, nodes, proposals, decisions))
    print(f"Generated {len(events)} events for {count} tags in {elapsed:.3f}s")
