"""Compare every proposed hierarchy concept with the current category/speciality pool.

Exact spelling variants can reuse one node under the same parent. Other matches
remain evidence for review; neither a score nor a cross-level name match permits
moving a node. Saved semantic evidence can be replayed without loading a model.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from typing import Any

import numpy as np

from ..classification.deduplication import SimilarityMatcher
from ..classification.models import HierarchyGroup
from ..datasets.base import TaxonomyDataset
from ..metrics.integrity import normalize
from ..metrics.saturation import lexical_scores, match_key
from .intake import collect_proposals
from .models import (
    ConceptMatch,
    ExistingConcept,
    ProposalScreeningReport,
    ScreenedProposal,
    ScreeningSettings,
)


# Serialize baseline identities without equating malformed IDs with valid integer IDs.
def _encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# Index only unique positive integer IDs with nonblank names for safe parent lookup.
def _index(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    counts = Counter(_encoded(row.get("id")) for row in rows)
    return {
        row["id"]: row for row in rows
        if type(row.get("id")) is int and row["id"] > 0 and counts[_encoded(row["id"])] == 1
        and isinstance(row.get("name"), str) and bool(normalize(row["name"]))
    }


# Preserve each named baseline candidate and its full branch, marking broken paths as non-reusable.
def _existing_concepts(taxonomy: TaxonomyDataset) -> list[ExistingConcept]:
    services, categories = _index(taxonomy.services), _index(taxonomy.categories)
    concepts = []
    for level, rows in (("category", taxonomy.categories), ("speciality", taxonomy.specialities)):
        valid = _index(rows)
        for row in rows:
            if not isinstance(row.get("name"), str) or not normalize(row["name"]):
                continue
            parent_id = row.get("category_id")
            category = row if level == "category" else (
                categories.get(parent_id) if type(parent_id) is int else None
            )
            service_id = category.get("service_id") if category else None
            service = services.get(service_id) if type(service_id) is int else None
            identifier = row.get("id")
            path = [service["name"] if service else None]
            path_ids = [service["id"] if service else None]
            if level == "speciality":
                path.append(category["name"] if category else None)
                path_ids.append(category["id"] if category else None)
            path.append(row["name"])
            path_ids.append(identifier if type(identifier) in {int, str} else None)
            concepts.append(ExistingConcept(
                reference=hashlib.sha256(_encoded([level, row]).encode("utf-8")).hexdigest(),
                level=level, id=path_ids[-1], name=row["name"], path=path, path_ids=path_ids,
                reusable=type(identifier) is int and identifier in valid and service is not None
                and category is not None,
            ))
    return sorted(concepts, key=lambda concept: (concept.level, concept.reference))


# Run optional rectangular semantic scoring once and make failed requested checks explicit.
def _semantic_scores(
    proposals: list[ScreenedProposal], concepts: list[ExistingConcept], matcher: SimilarityMatcher | None,
) -> tuple[np.ndarray | None, str, dict[str, str]]:
    if matcher is None:
        skipped = {"semantic": "No semantic matcher supplied; name and lexical screening ran"} if proposals else {}
        return None, "disabled", skipped
    if not proposals or not concepts:
        return np.zeros((len(proposals), len(concepts))), "complete", {}
    try:
        scores = np.asarray(matcher.similarity(
            [normalize(proposal.name) for proposal in proposals],
            [normalize(concept.name) for concept in concepts],
        ), dtype=float)
        if scores.shape != (len(proposals), len(concepts)) or not np.isfinite(scores).all():
            raise ValueError("Expected a finite proposal-by-baseline similarity matrix")
        if np.any(scores < -1.000001) or np.any(scores > 1.000001):
            raise ValueError("Cosine similarities must be between -1 and 1")
        return np.clip(scores, -1, 1), "complete", {}
    except Exception as exc:
        return None, "failed", {"semantic": f"Semantic screening failed: {type(exc).__name__}: {exc}"}


# Prefer proven reuse within the parent and otherwise retain uncertain additions for review.
def _classify(
    proposal: ScreenedProposal, original_id: int | None, parent_blocked: bool, semantic_failed: bool,
) -> None:
    exact = [match for match in proposal.matches if {"normalized_name", "match_key"} & set(match.match_types)]
    local = [match.concept for match in exact if match.same_parent and match.concept.reusable]
    known = [concept for concept in local if concept.id == original_id] if original_id is not None else []
    target = known[0] if len(known) == 1 else local[0] if len(local) == 1 else None
    if target is not None and not parent_blocked:
        proposal.status, proposal.existing_id = "reuse", target.id
        proposal.reason = "Exact name or spelling variant identifies an existing concept within the same parent"
        return
    proposal.blocks_creation = bool(exact) or parent_blocked or semantic_failed
    if proposal.matches or proposal.blocks_creation:
        proposal.status = "review"
        if parent_blocked:
            proposal.reason = "Resolve the proposed parent before adding a child concept"
        elif exact:
            proposal.reason = "Exact match needs parent, level or ambiguity review; do not create another concept"
        elif semantic_failed:
            proposal.reason = "Requested semantic screening failed; rerun before creating this concept"
        else:
            proposal.reason = "Possible near duplicate; confirm the proposed concept and placement before adding it"
    else:
        proposal.reason = "No exact or near-duplicate candidates found by the enabled checks"


# Screen every hierarchy path, retaining duplicate IDs and cross-branch evidence without collapsing them.
def screen_proposals(
    groups: Sequence[HierarchyGroup], taxonomy: TaxonomyDataset, *, baseline_snapshot_id: str,
    settings: ScreeningSettings | None = None, semantic_matcher: SimilarityMatcher | None = None,
    replay: ProposalScreeningReport | None = None,
) -> ProposalScreeningReport:
    if replay is not None:
        if semantic_matcher is not None or replay.baseline_snapshot_id != baseline_snapshot_id:
            raise ValueError("Screening replay requires its original baseline and no new semantic matcher")
        settings = replay.settings
    settings = ScreeningSettings.model_validate_json((settings or ScreeningSettings()).model_dump_json())
    proposals, concepts = collect_proposals(groups), _existing_concepts(taxonomy)
    keys, existing_keys = [match_key(item.name) for item in proposals], [match_key(item.name) for item in concepts]
    lexical = lexical_scores(keys, existing_keys)
    if replay is None:
        semantic, semantic_status, skipped = _semantic_scores(proposals, concepts, semantic_matcher)
        saved_scores = {}
    else:
        semantic, semantic_status, skipped = None, replay.semantic_status, replay.skipped_checks
        saved_scores = {
            (proposal.proposal_id, match.concept.reference): match.semantic_score
            for proposal in replay.proposals for match in proposal.matches
        }
    services = _index(taxonomy.services)
    existing_ids = {tuple(group.path): group.existing_id for group in groups}
    by_path: dict[tuple[str, ...], ScreenedProposal] = {}
    for index, proposal in enumerate(proposals):
        parent = by_path.get(tuple(proposal.path[:2])) if proposal.level == "speciality" else None
        service_ids = [identifier for identifier, row in services.items()
                       if normalize(row["name"]) == normalize(proposal.path[0])]
        parent_id = parent.existing_id if parent else service_ids[0] if len(service_ids) == 1 else None
        for column, concept in enumerate(concepts):
            score = float(lexical[index, column]) / 100
            meaning = float(semantic[index, column]) if semantic is not None else saved_scores.get(
                (proposal.proposal_id, concept.reference),
            )
            kinds = []
            if normalize(proposal.name) == normalize(concept.name):
                kinds.append("normalized_name")
            elif keys[index] and keys[index] == existing_keys[column]:
                kinds.append("match_key")
            if keys[index] and existing_keys[column] and score >= settings.lexical_threshold:
                kinds.append("lexical")
            if meaning is not None and meaning >= settings.semantic_threshold:
                kinds.append("semantic")
            if kinds:
                proposal.matches.append(ConceptMatch(
                    concept=concept, match_types=kinds, lexical_score=score, semantic_score=meaning,
                    same_parent=concept.level == proposal.level and parent_id is not None
                    and concept.path_ids[-2] == parent_id,
                ))
        _classify(proposal, existing_ids[tuple(proposal.path)], bool(parent and parent.blocks_creation),
                  semantic_status == "failed")
        by_path[tuple(proposal.path)] = proposal
    report = ProposalScreeningReport(
        baseline_snapshot_id=baseline_snapshot_id, settings=settings, semantic_status=semantic_status,
        skipped_checks=skipped, proposals=proposals,
    )
    if replay is not None and report != replay:
        raise ValueError("Screening evidence changed or is inconsistent; rerun merge-preview")
    return report
