"""Apply the existing ReviewDecision contract to screened hierarchy proposals.

Screening stays immutable evidence. This module derives the effective hierarchy
for resolution and replay; it neither writes taxonomy rows nor records new
review events during preview. Category decisions precede speciality decisions.
It reruns screening's own steps (`proposals.screening`'s public helpers) under
the reviewed parents, so the two can't drift apart.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ..matching.names import normalize
from ..taxonomy.base import TaxonomyDataset
from .models import ReviewDecision
from .proposals.models import ConceptMatch, ProposalScreeningReport, ScreenedProposal
from .proposals.screening import (
    classify_proposal,
    existing_concepts,
    hold_incoming_variants,
    is_same_parent,
    service_ids_by_name,
)

# An approved synonym identifies an existing concept as surely as its name does (#60).
EXACT_MATCH_TYPES = {"normalized_name", "match_key", "synonym"}


# Canonicalize hierarchy decisions and reject stale proposal IDs or conflicting actions.
def prepare_hierarchy_decisions(
    decisions: Sequence[ReviewDecision], screening: ProposalScreeningReport,
) -> list[ReviewDecision]:
    proposals = {proposal.proposal_id for proposal in screening.proposals}
    unique: dict[str, ReviewDecision] = {}
    for original in decisions:
        decision = ReviewDecision.model_validate_json(original.model_dump_json())
        if decision.proposal_id is None:
            continue
        if decision.proposal_id not in proposals:
            raise ValueError(f"Unknown hierarchy proposal: {decision.proposal_id}; rerun merge-preview")
        previous = unique.get(decision.proposal_id)
        if previous is not None and previous != decision:
            raise ValueError(f"Conflicting decisions for hierarchy proposal: {decision.proposal_id}")
        unique[decision.proposal_id] = decision
    return [unique[key] for key in sorted(unique)]


CreateBlocker = Literal["exact_match", "spelling_variant", "semantic_failed"]


# An exact match that stops `create`: anything but a same-named speciality under another category.
def blocks_create(proposal: ScreenedProposal, match: ConceptMatch) -> bool:
    if not EXACT_MATCH_TYPES & set(match.match_types):
        return False
    cross_parent_speciality = proposal.level == "speciality" and match.concept.level == "speciality" \
        and not match.same_parent
    return not cross_parent_speciality


# Reuse screening evidence to resolve reviewed parents and validate each hierarchy action.
def apply_hierarchy_decisions(
    screening: ProposalScreeningReport, taxonomy: TaxonomyDataset, decisions: Sequence[ReviewDecision],
) -> ProposalScreeningReport:
    prepared = prepare_hierarchy_decisions(decisions, screening)
    effective = screening.model_copy(deep=True)
    if not prepared:
        return effective
    by_id = {decision.proposal_id: decision for decision in prepared}
    by_path = {tuple(proposal.path): proposal for proposal in effective.proposals}
    targets = {(concept.level, concept.id): concept for concept in existing_concepts(taxonomy) if concept.reusable}
    services = service_ids_by_name(taxonomy)
    excluded = {key for key, decision in by_id.items() if decision.action == "exclude"}
    semantic_failed = screening.semantic_status == "failed"

    def service_id(proposal: ScreenedProposal) -> int | None:
        ids = services.get(normalize(proposal.path[0]), [])
        return ids[0] if len(ids) == 1 else None

    def parent_of(proposal: ScreenedProposal) -> ScreenedProposal | None:
        return by_path.get(tuple(proposal.path[:2])) if proposal.level == "speciality" else None

    for level in ("category", "speciality"):
        current = [proposal for proposal in effective.proposals if proposal.level == level]
        # Reconsider children under an explicitly selected parent, using the original scores.
        for proposal in current:
            parent = parent_of(proposal)
            parent_id = parent.existing_id if parent else service_id(proposal)
            for match in proposal.matches:
                match.same_parent = is_same_parent(match.concept, level, parent_id)
            original_id = proposal.existing_id
            proposal.existing_id, proposal.blocks_creation, proposal.status = None, False, "new"
            classify_proposal(proposal, original_id, bool(parent and parent.blocks_creation), semantic_failed)

        # Reuse, exclude and defer first: a variant the reviewer resolved no longer holds its partner.
        decided = [(proposal, by_id[proposal.proposal_id]) for proposal in current if proposal.proposal_id in by_id]
        for proposal, decision in decided:
            if decision.action in {"exclude", "defer"}:
                proposal.status, proposal.existing_id, proposal.blocks_creation = "review", None, True
            elif decision.action == "reuse":
                _require_parent_resolved(proposal, parent_of(proposal))
                proposal.status, proposal.existing_id = "reuse", _reuse_target(
                    proposal, decision, parent_of(proposal), service_id(proposal), targets,
                )
                proposal.blocks_creation = False
        # Rejected alternatives no longer compete with a retained incoming spelling variant.
        held = hold_incoming_variants(
            [proposal for proposal in effective.proposals if proposal.proposal_id not in excluded],
        )
        for proposal, decision in decided:
            if decision.action == "create":
                _require_parent_resolved(proposal, parent_of(proposal))
                _check_create(proposal, held, semantic_failed)
                proposal.status, proposal.existing_id, proposal.blocks_creation = "new", None, False
            proposal.reason = f"Reviewer {decision.action}: {decision.reason}"
    return effective


def _require_parent_resolved(proposal: ScreenedProposal, parent: ScreenedProposal | None) -> None:
    if parent is not None and parent.blocks_creation:
        raise ValueError(f"Resolve the parent before approving proposal {proposal.proposal_id}")


def create_blocker(proposal: ScreenedProposal, held: set[str], semantic_failed: bool) -> CreateBlocker | None:
    """What stops `create` for this proposal, if anything, shared by the check below and the review queue."""
    if any(blocks_create(proposal, match) for match in proposal.matches):
        return "exact_match"
    if proposal.proposal_id in held:
        return "spelling_variant"
    return "semantic_failed" if semantic_failed else None


def _check_create(proposal: ScreenedProposal, held: set[str], semantic_failed: bool) -> None:
    """`create` is refused for an exact equivalent in the same parent or level, an unresolved incoming
    spelling variant, or a failed requested semantic check. A same-named speciality under another category
    doesn't stop it: `integrate` allows cross-parent duplicate names too."""
    blocker = create_blocker(proposal, held, semantic_failed)
    if blocker == "exact_match":
        raise ValueError(f"Cannot create proposal {proposal.proposal_id}: exact/equivalent match exists")
    if blocker == "spelling_variant":
        raise ValueError(f"Cannot create proposal {proposal.proposal_id}: {proposal.reason}")
    if blocker == "semantic_failed":
        raise ValueError(f"Cannot create proposal {proposal.proposal_id}: requested semantic screening failed; "
                         "rerun merge-preview")


def _reuse_target(
    proposal: ScreenedProposal, decision: ReviewDecision, parent: ScreenedProposal | None,
    service_id: int | None, targets: dict,
) -> int:
    """The existing node a `reuse` points at, which must sit under the proposal's own parent."""
    target = targets.get((proposal.level, decision.existing_concept_id))
    if target is None:
        raise ValueError(f"Merge target must identify one valid existing {proposal.level}: "
                         f"{decision.existing_concept_id}")
    if parent is not None and target.path_ids[-2] != parent.existing_id:
        raise ValueError(f"Merge target {target.id} belongs to a different category; reuse its parent category first")
    if proposal.level == "category" and target.path_ids[0] != service_id:
        raise ValueError(f"Merge target {target.id} belongs to a different service than {proposal.path[0]!r}; "
                         "a category can only be reused within its own service")
    return target.id
