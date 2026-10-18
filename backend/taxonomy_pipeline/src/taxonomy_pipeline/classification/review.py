"""Apply the existing ReviewDecision contract to screened hierarchy proposals.

Screening stays immutable evidence. This module derives the effective hierarchy
for resolution and replay; it neither writes taxonomy rows nor records new
review events during preview. Category decisions precede speciality decisions.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..datasets.base import TaxonomyDataset
from ..metrics.integrity import normalize
from ..proposals.models import ProposalScreeningReport
from ..proposals.screening import _classify, _existing_concepts, _hold_incoming_variants
from .models import ReviewDecision


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
    concepts = _existing_concepts(taxonomy)
    targets = {(concept.level, concept.id): concept for concept in concepts if concept.reusable}
    services = {normalize(row["name"]): row["id"] for row in taxonomy.services
                if type(row.get("id")) is int and isinstance(row.get("name"), str)}
    excluded = {key for key, decision in by_id.items() if decision.action == "exclude"}
    for level in ("category", "speciality"):
        current = [proposal for proposal in effective.proposals if proposal.level == level]
        # Reconsider children under an explicitly selected parent, using the original scores.
        for proposal in current:
            parent = by_path.get(tuple(proposal.path[:2])) if level == "speciality" else None
            parent_id = parent.existing_id if parent else services.get(normalize(proposal.path[0]))
            for match in proposal.matches:
                match.same_parent = (
                    match.concept.level == level and parent_id is not None
                    and match.concept.path_ids[-2] == parent_id
                )
            original_id = proposal.existing_id
            proposal.existing_id, proposal.blocks_creation, proposal.status = None, False, "new"
            _classify(proposal, original_id, bool(parent and parent.blocks_creation),
                      screening.semantic_status == "failed")
        # Rejected alternatives no longer compete with a retained incoming spelling variant.
        _hold_incoming_variants([proposal for proposal in effective.proposals if proposal.proposal_id not in excluded])
        for proposal in current:
            decision = by_id.get(proposal.proposal_id)
            if decision is None:
                continue
            parent = by_path.get(tuple(proposal.path[:2])) if level == "speciality" else None
            if decision.action in {"exclude", "defer"}:
                proposal.status, proposal.existing_id, proposal.blocks_creation = "review", None, True
            else:
                if parent is not None and parent.blocks_creation:
                    raise ValueError(f"Resolve the parent before approving proposal {proposal.proposal_id}")
                if decision.action == "create":
                    if any({"normalized_name", "match_key"} & set(match.match_types) for match in proposal.matches):
                        raise ValueError(
                            f"Cannot create proposal {proposal.proposal_id}: exact/equivalent match exists",
                        )
                    if proposal.blocks_creation:
                        raise ValueError(f"Cannot create proposal {proposal.proposal_id}: {proposal.reason}")
                    proposal.status, proposal.existing_id = "new", None
                else:
                    target = targets.get((level, decision.existing_concept_id))
                    if target is None:
                        raise ValueError(f"Merge target must identify one valid existing {level}: "
                                         f"{decision.existing_concept_id}")
                    if parent is not None and target.path_ids[-2] != parent.existing_id:
                        raise ValueError(f"Merge target {target.id} belongs to a different category; "
                                         "reuse its parent category first")
                    proposal.status, proposal.existing_id = "reuse", target.id
                proposal.blocks_creation = False
            proposal.reason = f"Reviewer {decision.action}: {decision.reason}"
    return effective
