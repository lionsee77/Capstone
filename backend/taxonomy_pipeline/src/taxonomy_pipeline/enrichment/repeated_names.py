"""Advisory collisions over live proposals, shared by persisted screening and review views.

Pending choices remain evidence; withdrawn branches and declined alternatives do
not. Findings never change screening holds, approvals, or taxonomy identities.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ..matching.names import match_key
from ..taxonomy.base import TaxonomyDataset
from .models import DeduplicationReport, ReviewDecision
from .paths import PathKey, placement_path
from .proposals.models import ProposalScreeningReport, RepeatedNameWarning, ScreenedProposal

PlacementKey = tuple[str, str, PathKey]


# Retain undecided paths and explicit approvals, dropping every withdrawn hierarchy branch.
def live_placements(
    evidence: DeduplicationReport, screening: ProposalScreeningReport, decisions: Sequence[ReviewDecision],
) -> set[PlacementKey]:
    withdrawn = {item.proposal_id for item in decisions if item.action in {"exclude", "defer"}}
    withdrawn_paths = {tuple(item.path) for item in screening.proposals if item.proposal_id in withdrawn}
    specialities = {tuple(item.path): item.existing_id for item in screening.proposals if item.level == "speciality"}
    choices = {(member.source_dataset, member.tag_id): decision
               for decision in decisions for member in decision.members}
    approvals = {key: {placement_path(path) for path in decision.approved_paths} for key, decision in choices.items()}
    live = set()
    for tag in evidence.tags:
        source = tag.source.source_dataset, tag.source.tag_id
        decision = choices.get(source)
        for row in tag.placements:
            path = placement_path(row)
            if path in withdrawn_paths or path[:2] in withdrawn_paths:
                continue
            if decision is not None and (
                decision.action in {"exclude", "defer"}
                or path not in approvals[source] and specialities.get(path) not in decision.approved_speciality_ids
            ):
                continue
            live.add((*source, path))
    return live


# Describe one shared parent, retaining resolved IDs and grouping unresolved spelling variants.
def _parent(
    proposal: ScreenedProposal, categories: dict[tuple[str, ...], ScreenedProposal],
    baseline_categories: dict[int, dict], services: dict[int, str],
) -> tuple[tuple[str, ...], str]:
    service = proposal.path[0]
    if proposal.level == "category":
        return (service,), service
    parent = categories[tuple(proposal.path[:2])]
    if parent.existing_id is not None:
        category = baseline_categories[parent.existing_id]
        label = f"{services[category['service_id']]} > {category['name']} (category ID {parent.existing_id})"
        return ("category_id", str(parent.existing_id)), label
    if parent.blocks_creation:
        key = match_key(parent.name)
        return (service, key), f"{service} > {key} (parent awaiting review)"
    return tuple(parent.path), " > ".join(min(parent.variants))


# Build deterministic clusters without mutating proposals or presentation rows.
def repeated_name_warnings(
    screening: ProposalScreeningReport, live: set[PlacementKey], baseline: TaxonomyDataset,
) -> list[RepeatedNameWarning]:
    active = {key for _, _, path in live for key in (path[:2], path)}
    categories = {tuple(item.path): item for item in screening.proposals if item.level == "category"}
    baseline_categories = {row["id"]: row for row in baseline.categories}
    services = {row["id"]: row["name"] for row in baseline.services}
    clusters: dict[tuple[str, str], dict[tuple[str, ...], list[ScreenedProposal]]] = defaultdict(
        lambda: defaultdict(list),
    )
    labels = {}
    for proposal in screening.proposals:
        path = tuple(proposal.path)
        if path not in active or proposal.existing_id is not None:
            continue
        identity, label = _parent(proposal, categories, baseline_categories, services)
        labels[(proposal.level, identity)] = label
        clusters[(proposal.level, path[-1])][identity].append(proposal)
    warnings = []
    for (level, name), parents in sorted(clusters.items()):
        if len(parents) < 2:
            continue
        groups, identifiers = [], []
        for identity, proposals in sorted(parents.items()):
            members = []
            for proposal in sorted(proposals, key=lambda item: (item.path, item.proposal_id)):
                identifiers.append(proposal.proposal_id)
                members.append(f"{proposal.proposal_id}: {' > '.join(min(proposal.variants))}")
            groups.append(f"Parent {labels[(level, identity)]}: [" + "; ".join(members) + "]")
        warnings.append(RepeatedNameWarning(
            code=f"warning.repeated_{level}_name", name=name, proposal_ids=identifiers,
            message=(f"Repeated new {level} name {name!r} across {len(parents)} distinct parents. "
                     + "; ".join(groups) + ". Advisory only; placements and approvals are unchanged."),
        ))
    return warnings
