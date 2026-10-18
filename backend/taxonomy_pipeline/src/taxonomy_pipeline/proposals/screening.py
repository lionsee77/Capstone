"""Compare every proposed hierarchy concept with the current category/speciality pool.

Exact spelling variants can reuse one node under the same parent. Other matches
remain evidence for review; neither a score nor a cross-level name match permits
moving a node. Saved semantic evidence can be replayed without loading a model.
Incoming spelling variants under the same parent are held before creating nodes.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

import numpy as np

from ..datasets.base import TaxonomyDataset
from ..matching import SimilarityMatcher
from ..metrics.integrity import normalize
from ..metrics.saturation import lexical_scores, match_key
from ..models import HierarchyGroup
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
    names: list[str], existing_names: list[str], matcher: SimilarityMatcher | None,
) -> tuple[np.ndarray | None, str, dict[str, str]]:
    if matcher is None:
        skipped = {"semantic": "No semantic matcher supplied; name and lexical screening ran"} if names else {}
        return None, "disabled", skipped
    if not names or not existing_names:
        return np.zeros((len(names), len(existing_names))), "complete", {}
    try:
        scores = np.asarray(matcher.similarity(names, existing_names), dtype=float)
        if scores.shape != (len(names), len(existing_names)) or not np.isfinite(scores).all():
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


# Hold incoming spelling variants within one parent before any dependent placements can proceed.
def _hold_incoming_variants(proposals: list[ScreenedProposal]) -> None:
    by_path = {tuple(proposal.path): proposal for proposal in proposals}
    for level in ("category", "speciality"):
        clusters: dict[tuple[str, str], list[ScreenedProposal]] = defaultdict(list)
        for proposal in proposals:
            if proposal.level != level:
                continue
            parent = by_path.get(tuple(proposal.path[:2])) if level == "speciality" else None
            if parent is not None and parent.blocks_creation:
                proposal.status, proposal.blocks_creation = "review", True
                proposal.reason = "Resolve the proposed parent before adding a child concept"
            if proposal.existing_id is not None:
                continue
            parent_identity = (
                ["category_id", parent.existing_id] if parent is not None and parent.existing_id is not None
                else proposal.path[:-1]
            )
            key = match_key(proposal.name)
            if key:
                clusters[(_encoded(parent_identity), key)].append(proposal)
        for cluster in clusters.values():
            if len({normalize(proposal.name) for proposal in cluster}) < 2:
                continue
            identifiers = ", ".join(sorted(proposal.proposal_id for proposal in cluster))
            for proposal in cluster:
                proposal.status, proposal.blocks_creation = "review", True
                proposal.reason = (
                    "Incoming proposals share a match_key within the same parent; "
                    f"resolve the spelling variants before creating a concept. Proposal IDs: {identifiers}"
                )


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
    names, existing_names = [normalize(item.name) for item in proposals], [normalize(item.name) for item in concepts]
    keys, existing_keys = [match_key(item.name) for item in proposals], [match_key(item.name) for item in concepts]
    exact_columns: dict[str, set[int]] = defaultdict(set)
    key_columns: dict[str, set[int]] = defaultdict(set)
    for column, (name, key) in enumerate(zip(existing_names, existing_keys, strict=True)):
        exact_columns[name].add(column)
        if key:
            key_columns[key].add(column)
    services_by_name: dict[str, list[int]] = defaultdict(list)
    for identifier, row in _index(taxonomy.services).items():
        services_by_name[normalize(row["name"])].append(identifier)
    for name in sorted({normalize(proposal.path[0]) for proposal in proposals}):
        if len(services_by_name[name]) > 1:
            raise ValueError(f"Ambiguous baseline service name {name!r}: IDs {sorted(services_by_name[name])}")
    lexical = lexical_scores(keys, existing_keys)
    valid_keys = np.array([bool(key) for key in existing_keys], dtype=bool)
    if replay is None:
        semantic, semantic_status, skipped = _semantic_scores(names, existing_names, semantic_matcher)
        saved_scores = {}
    else:
        semantic, semantic_status, skipped = None, replay.semantic_status, replay.skipped_checks
        references: dict[str, list[int]] = defaultdict(list)
        for column, concept in enumerate(concepts):
            references[concept.reference].append(column)
        saved_scores = {
            proposal.proposal_id: {column: match.semantic_score for match in proposal.matches
                                   for column in references[match.concept.reference]}
            for proposal in replay.proposals
        }
    existing_ids = {tuple(group.path): group.existing_id for group in groups}
    by_path: dict[tuple[str, ...], ScreenedProposal] = {}
    for index, proposal in enumerate(proposals):
        parent = by_path.get(tuple(proposal.path[:2])) if proposal.level == "speciality" else None
        service_ids = services_by_name[normalize(proposal.path[0])]
        parent_id = parent.existing_id if parent else service_ids[0] if len(service_ids) == 1 else None
        exact = exact_columns.get(names[index], set())
        key_matches = key_columns.get(keys[index], set())
        lexical_matches = set(np.flatnonzero(
            (lexical[index] / 100 >= settings.lexical_threshold) & valid_keys,
        )) if keys[index] else set()
        saved = saved_scores.get(proposal.proposal_id, {})
        semantic_matches = set(np.flatnonzero(
            semantic[index] >= settings.semantic_threshold,
        )) if semantic is not None else {
            column for column, score in saved.items() if score is not None and score >= settings.semantic_threshold
        }
        for column in sorted(exact | key_matches | lexical_matches | semantic_matches):
            concept = concepts[column]
            score = float(lexical[index, column]) / 100
            meaning = float(semantic[index, column]) if semantic is not None else saved.get(column)
            kinds = []
            if column in exact:
                kinds.append("normalized_name")
            elif column in key_matches:
                kinds.append("match_key")
            if column in lexical_matches:
                kinds.append("lexical")
            if column in semantic_matches:
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
    _hold_incoming_variants(proposals)
    report = ProposalScreeningReport(
        baseline_snapshot_id=baseline_snapshot_id, settings=settings, semantic_status=semantic_status,
        skipped_checks=skipped, proposals=proposals,
    )
    if replay is not None and report != replay:
        raise ValueError("Screening evidence changed or is inconsistent; rerun merge-preview")
    return report
