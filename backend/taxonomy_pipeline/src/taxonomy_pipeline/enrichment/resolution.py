"""Resolve reviewed concepts and placements independently of producer export formats.

Inputs are a deduplication report, its baseline, and optional review decisions.
Unflagged tags retain their supplied classifications; source duplicate candidates
and normalized baseline name matches need review. No automatic tag merging happens here. Temporary IDs describe proposed
nodes; assigning database IDs and building the combined preview are later steps.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from ..matching.semantic import SimilarityMatcher
from ..quality.integrity import normalize
from ..taxonomy.base import TaxonomyDataset
from .models import (
    CanonicalMapping,
    CanonicalTag,
    DeduplicationReport,
    ResolutionReport,
    ResolvedHierarchyNode,
    ResolvedLink,
    ReviewDecision,
    SourceDisposition,
    SourceTagRef,
    TagProposal,
    TaxonomyPath,
)
from .proposals.models import ProposalScreeningReport, ScreeningSettings
from .proposals.screening import screen_proposals
from .review import apply_hierarchy_decisions, prepare_hierarchy_decisions
from .validation import resolve_placement_path

SourceKey = tuple[str, str]
PathKey = tuple[str, str, str]


# Encode structured identities without delimiter collisions or dependence on iteration order.
def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# Keep equal source IDs from different datasets distinct throughout consolidation.
def _source_key(source: SourceTagRef) -> SourceKey:
    return source.source_dataset, source.tag_id


# Match reviewed paths using the same parent-scoped normalization as intake and deduplication.
def _path_key(path: TaxonomyPath) -> PathKey:
    return path.service, normalize(path.category), normalize(path.speciality)


# Index eligible source evidence and reject contradictory or unresolved deduplication records.
def _index_tags(report: DeduplicationReport) -> dict[SourceKey, TagProposal]:
    tags = {}
    for tag in report.tags:
        key = _source_key(tag.source)
        if key in tags or not tag.placements:
            raise ValueError("Deduplication must contain one nonempty proposal per source identity")
        for placement in tag.placements:
            if (
                (placement.source_dataset, placement.tag_id) != key
                or placement.tag != tag.tag or placement.original_classification != tag.original_classification
                or placement.service is None
            ):
                raise ValueError(f"Inconsistent source placement: {key}")
        tags[key] = tag
    return tags


# Recover every distinct proposed path for a source without discarding raw labels in its evidence.
def _tag_paths(tag: TagProposal) -> dict[PathKey, TaxonomyPath]:
    paths = {}
    for row in sorted(tag.placements, key=lambda row: row.model_dump_json()):
        path = TaxonomyPath(service=row.service, category=row.category, speciality=row.speciality)
        paths.setdefault(_path_key(path), path)
    return paths


# Recheck path reuse against the baseline and assign deterministic IDs before constructing any links.
def _resolve_hierarchy(
    report: DeduplicationReport, taxonomy: TaxonomyDataset, tags: dict[SourceKey, TagProposal],
    screening: ProposalScreeningReport,
) -> dict[tuple[str, ...], ResolvedHierarchyNode]:
    expected: set[tuple[str, ...]] = set()
    for tag in tags.values():
        for path in _tag_paths(tag):
            expected.update((path[:2], path))
    groups = {tuple(group.path): group for group in report.hierarchy}
    if len(groups) != len(report.hierarchy) or set(groups) != expected:
        raise ValueError("Hierarchy groups must match the source placement paths exactly")
    nodes: dict[tuple[str, ...], ResolvedHierarchyNode] = {}
    screened = {tuple(proposal.path): proposal for proposal in screening.proposals}
    if not groups.keys() <= screened.keys():
        raise ValueError("Screening must cover every eligible hierarchy path")
    # Categories must be resolved before their specialities, independently of report order.
    for key in sorted(groups, key=lambda key: (len(key), key)):
        group = groups[key]
        size = 2 if group.level == "category" else 3
        if len(key) != size or not group.variants:
            raise ValueError("Invalid hierarchy group level or missing label variants")
        for variant in group.variants:
            if len(variant) != size or (variant[0], *(normalize(name) for name in variant[1:])) != key:
                raise ValueError("Hierarchy labels do not match their normalized path")
        label = min(group.variants)[-1]
        # Resolving a category needs any real child path so intake's resolver can be reused unchanged.
        child = key if size == 3 else next(path for path in sorted(expected) if len(path) == 3 and path[:2] == key)
        target = resolve_placement_path(dict(zip(("service", "category", "speciality"), child, strict=True)), taxonomy)
        existing_id = target.category_id if size == 2 else target.speciality_id
        if existing_id != group.existing_id:
            raise ValueError(f"Baseline path changed; rerun validation and deduplication: {key}")
        existing_id = screened[key].existing_id
        parent_id = target.service_id if size == 2 else nodes[key[:2]].id
        # Parent spelling variants that reuse one category must share any new child node as well. The
        # category's real service names the identity, so a reused category in the baseline keeps one ID.
        identity = key
        if size == 3:
            service = next((row["name"] for row in taxonomy.services if row["id"] == nodes[key[:2]].parent_id), None)
            if not isinstance(service, str):
                raise ValueError(f"Category {nodes[key[:2]].id} has no named baseline service; check the baseline")
            identity = (service, normalize(nodes[key[:2]].name), key[2])
        if existing_id is not None:
            rows = taxonomy.categories if size == 2 else taxonomy.specialities
            existing = next(row for row in rows if row["id"] == existing_id)
            label = existing["name"]
            parent_id = existing["service_id" if size == 2 else "category_id"]
        nodes[key] = ResolvedHierarchyNode(
            level=group.level, id=existing_id or f"new:{group.level}:{_digest(identity)}",
            parent_id=parent_id, name=label, path=list(key), existing=existing_id is not None,
        )
    return nodes


# Canonicalize repeated decisions while rejecting overlapping groups and unknown source identities.
def _prepare_decisions(
    decisions: Sequence[ReviewDecision], tags: dict[SourceKey, TagProposal],
) -> list[ReviewDecision]:
    unique = {}
    for original in decisions:
        decision = ReviewDecision.model_validate_json(original.model_dump_json())
        if decision.proposal_id is not None:
            continue
        members = {_source_key(member): member for member in decision.members}
        if set(members) - set(tags):
            raise ValueError(f"Decision refers to unknown source tags: {sorted(set(members) - set(tags))}")
        decision.members = [members[key] for key in sorted(members)]
        decision.approved_speciality_ids = sorted(set(decision.approved_speciality_ids))
        paths = {path.model_dump_json(): path for path in decision.approved_paths}
        decision.approved_paths = [paths[key] for key in sorted(paths)]
        unique[decision.model_dump_json()] = decision
    prepared = [unique[key] for key in sorted(unique)]
    seen: set[SourceKey] = set()
    for decision in prepared:
        members = {_source_key(member) for member in decision.members}
        if members & seen:
            raise ValueError(f"Conflicting or overlapping decisions for source tags: {sorted(members & seen)}")
        seen.update(members)
    return prepared


# Identify baseline name matches without assuming that equal labels describe the same concept.
def _baseline_match_reasons(tags: dict[SourceKey, TagProposal], taxonomy: TaxonomyDataset) -> dict[SourceKey, str]:
    baseline_names: dict[str, set[str]] = {}
    for row in taxonomy.tags:
        name = row.get("name")
        if not isinstance(name, str):
            continue
        normalized = normalize(name)
        if normalized:
            evidence = json.dumps({"id": row.get("id"), "name": name}, ensure_ascii=False, sort_keys=True)
            baseline_names.setdefault(normalized, set()).add(evidence)
    reasons = {}
    for key, tag in tags.items():
        matches = baseline_names.get(normalize(tag.tag))
        if matches:
            reasons[key] = (
                "Potential duplicate: normalized name matches baseline tags: "
                + "; ".join(sorted(matches))
                + ". Review whether to reuse an existing tag or create a separate concept."
            )
    return reasons


# Hold tags whose producer flagged a placement (a candidate, or a tag saturation couldn't confirm as new).
def _producer_review_reasons(tags: dict[SourceKey, TagProposal]) -> dict[SourceKey, str]:
    reasons = {}
    for key, tag in tags.items():
        flagged = {_path_key(TaxonomyPath(service=row.service, category=row.category, speciality=row.speciality))
                   for row in tag.placements if row.needs_review}
        if flagged:
            reasons[key] = (
                f"Producer requested review: {len(flagged)} candidate placement(s); each placement's reason says "
                "why. Approve the right path with a create decision, map it to an existing tag with reuse, or "
                "exclude/defer the tag."
            )
    return reasons


# Retain every hierarchy, baseline and producer reason instead of overwriting earlier review context.
def _combined_review_reasons(
    tags: dict[SourceKey, TagProposal], taxonomy: TaxonomyDataset, screening: ProposalScreeningReport,
) -> dict[SourceKey, str]:
    reasons: dict[SourceKey, list[str]] = {}
    for proposal in screening.proposals:
        if proposal.status == "review":
            reason = (f"Hierarchy screening: {' > '.join(proposal.path)}: {proposal.reason}. "
                      "See resolution.screening for candidates and scores.")
            for source in proposal.source_tags:
                reasons.setdefault(source, []).append(reason)
    for additional in (_baseline_match_reasons(tags, taxonomy), _producer_review_reasons(tags)):
        for source, reason in additional.items():
            reasons.setdefault(source, []).append(reason)
    return {source: " ".join(dict.fromkeys(items)) for source, items in reasons.items()}


# Accept supplied placements only when nothing flags the source: no incoming duplicate candidate, no
# baseline name match, and no placement its producer marked for review.
def _default_decisions(
    report: DeduplicationReport, tags: dict[SourceKey, TagProposal], decisions: Sequence[ReviewDecision],
    flagged_reasons: dict[SourceKey, str],
) -> list[ReviewDecision]:
    flagged: set[SourceKey] = set(flagged_reasons)
    for pair in report.candidates:
        left, right = _source_key(pair.left), _source_key(pair.right)
        if left not in tags or right not in tags or left == right:
            raise ValueError("Duplicate candidates must reference two distinct eligible source tags")
        flagged.update((left, right))
    reviewed = {_source_key(member) for decision in decisions for member in decision.members}
    defaults = []
    for key in sorted(tags.keys() - flagged - reviewed):
        paths = _tag_paths(tags[key])
        defaults.append(ReviewDecision(
            members=[tags[key].source.model_copy(deep=True)], action="create",
            reason="Automatically accepted supplied classification: no duplicate candidate for this source tag",
            approved_paths=[paths[path] for path in sorted(paths)],
        ))
    return defaults


# Approve only placements supplied by group members, supporting both baseline IDs and new paths.
def _approved_paths(
    decision: ReviewDecision, tags: dict[SourceKey, TagProposal],
    nodes: dict[tuple[str, ...], ResolvedHierarchyNode],
) -> set[PathKey]:
    proposed = {path for source in decision.members for path in _tag_paths(tags[_source_key(source)])}
    approved = {_path_key(path) for path in decision.approved_paths}
    if approved - proposed:
        raise ValueError("Approved paths must be proposed placements of this decision's members")
    by_id: dict[int | str, set[PathKey]] = {}
    for path in proposed:
        if nodes[path].existing:
            by_id.setdefault(nodes[path].id, set()).add(path)
    for identifier in decision.approved_speciality_ids:
        if identifier not in by_id:
            raise ValueError(f"Approved speciality {identifier} is not an existing proposed placement of this group")
        approved.update(by_id[identifier])
    return approved


# Use an explicitly approved baseline tag or select a source label for a newly accepted concept.
def _canonical_tag(
    decision: ReviewDecision, tags: dict[SourceKey, TagProposal], taxonomy: TaxonomyDataset,
) -> CanonicalTag:
    if decision.action == "reuse":
        matches = [row for row in taxonomy.tags if row.get("id") == decision.existing_tag_id]
        if (
            len(matches) != 1 or type(matches[0].get("id")) is not int
            or not isinstance(matches[0].get("name"), str) or not matches[0]["name"].strip()
        ):
            raise ValueError(f"Existing tag ID must identify one valid baseline tag: {decision.existing_tag_id}")
        identifier, name = decision.existing_tag_id, matches[0]["name"]
    else:
        names = {tags[_source_key(member)].tag for member in decision.members}
        name = decision.canonical_name
        if name is None and len(names) == 1:
            name = next(iter(names))
        if name not in names:
            raise ValueError("Choose canonical_name from the group's source labels when they differ")
        identifier = f"new:tag:{_digest(sorted(_source_key(member) for member in decision.members))}"
    return CanonicalTag(id=identifier, name=name, existing=decision.action == "reuse", members=decision.members)


# Fingerprint the supplied baseline independently of table row ordering.
def taxonomy_snapshot_id(taxonomy: TaxonomyDataset) -> str:
    data = taxonomy.model_dump(mode="json")
    for key, value in data.items():
        if isinstance(value, list):
            data[key] = sorted(value, key=lambda row: json.dumps(row, sort_keys=True))
    return f"sha256:{_digest(data)}"


# Resolve reviewed identities and approved links without mutating source evidence or the baseline.
def resolve_outputs(
    report: DeduplicationReport, taxonomy: TaxonomyDataset, decisions: Sequence[ReviewDecision] = (),
    *, semantic_matcher: SimilarityMatcher | None = None, screening: ProposalScreeningReport | None = None,
    screening_settings: ScreeningSettings | None = None,
) -> ResolutionReport:
    """Return stable canonical mappings and approved nodes for preview construction.

    Unflagged tags and their supplied paths are accepted by default. Source candidate
    participants, baseline name matches and tags with a producer `needs_review`
    placement stay pending without decisions. Explicit decisions take precedence.
    Separate accepted groups remain separate even
    when matching suggested equivalence; one multi-member decision confirms a merge.
    Contradictory decisions or changed baseline paths raise ValueError before a
    result is returned. Partial upstream evidence is retained and clearly marked.
    """
    if report.taxonomy != taxonomy.name:
        raise ValueError("The deduplication report and baseline taxonomy names must match")
    tags = _index_tags(report)
    snapshot_id = taxonomy_snapshot_id(taxonomy)
    screening = screen_proposals(
        report.screening_hierarchy if report.screening_hierarchy is not None else report.hierarchy,
        taxonomy, baseline_snapshot_id=snapshot_id,
        settings=screening_settings, semantic_matcher=semantic_matcher, replay=screening,
    )
    prepared = _prepare_decisions(decisions, tags)
    hierarchy_decisions = prepare_hierarchy_decisions(decisions, screening)
    effective = apply_hierarchy_decisions(screening, taxonomy, hierarchy_decisions)
    nodes = _resolve_hierarchy(report, taxonomy, tags, effective)
    blocked_paths = {tuple(proposal.path) for proposal in effective.proposals if proposal.blocks_creation}
    flagged_reasons = _combined_review_reasons(tags, taxonomy, effective)
    defaults = _default_decisions(report, tags, prepared, flagged_reasons)
    canonical: dict[int | str, CanonicalTag] = {}
    mappings: list[CanonicalMapping] = []
    dispositions: dict[SourceKey, SourceDisposition] = {}
    links: dict[tuple[int | str, int | str], ResolvedLink] = {}
    used_paths: set[tuple[str, ...]] = set()
    baseline_links = {
        (row["speciality_id"], row["tag_id"]) for row in taxonomy.speciality_tags
        if type(row.get("speciality_id")) is int and type(row.get("tag_id")) is int
    }
    for decision in prepared + defaults:
        accepted = decision.action in {"reuse", "create"}
        approved = _approved_paths(decision, tags, nodes)
        blocked = {
            path for path in approved if path in blocked_paths or path[:2] in blocked_paths
        } if accepted else set()
        if blocked:
            # Keep safe approved paths while retaining blocked placements for review.
            approved -= blocked
            reason = "Hierarchy screening blocked approved paths: " + "; ".join(" > ".join(p) for p in sorted(blocked))
            for member in decision.members:
                key = _source_key(member)
                flagged_reasons[key] = " ".join(filter(None, (flagged_reasons.get(key), reason)))
            if not approved:
                continue
        if accepted:
            tag = _canonical_tag(decision, tags, taxonomy)
            if tag.id in canonical:
                canonical[tag.id].members.extend(tag.members)
            else:
                canonical[tag.id] = tag.model_copy(deep=True)
            for path in sorted(approved):
                pair = nodes[path].id, tag.id
                links[pair] = ResolvedLink(speciality_id=pair[0], tag_id=pair[1], existing=pair in baseline_links)
                used_paths.update((path[:2], path))
            mappings.extend(CanonicalMapping(source=source, canonical_tag_id=tag.id) for source in decision.members)
        for source in decision.members:
            key = _source_key(source)
            paths = _tag_paths(tags[key])
            dispositions[key] = SourceDisposition(
                source=source,
                status="accepted" if accepted else "excluded" if decision.action == "exclude" else "deferred",
                reason=f"{decision.reason} {flagged_reasons[key]}" if blocked else decision.reason,
                unapproved_paths=[paths[path] for path in sorted(paths.keys() - approved)],
            )
    for key in sorted(tags.keys() - dispositions.keys()):
        paths = _tag_paths(tags[key])
        dispositions[key] = SourceDisposition(
            source=tags[key].source.model_copy(deep=True), status="pending",
            reason=flagged_reasons.get(
                key, "Potential duplicate: review whether to merge or keep this source tag separate",
            ),
            unapproved_paths=[paths[path] for path in sorted(paths)],
        )
    for tag in canonical.values():
        tag.members.sort(key=_source_key)
    hierarchy = [nodes[path] for path in sorted(used_paths, key=lambda path: (len(path), path))]
    resolved_tags = sorted(canonical.values(), key=lambda tag: (not tag.existing, str(tag.id)))
    resolved_links = sorted(links.values(), key=lambda link: (str(link.speciality_id), str(link.tag_id)))
    counts = {
        "source_tags": len(tags), "canonical_tags": len(canonical),
        "added_tags": sum(not tag.existing for tag in resolved_tags),
        "reused_tags": sum(tag.existing for tag in resolved_tags),
        "added_links": sum(not link.existing for link in resolved_links),
        "reused_links": sum(link.existing for link in resolved_links),
        "omitted_input_records": sum(report.omitted_counts.values()),
    }
    for status in ("accepted", "excluded", "deferred", "pending"):
        counts[f"{status}_sources"] = sum(item.status == status for item in dispositions.values())
    for level in ("category", "speciality"):
        counts[f"added_{level}_nodes"] = len({
            node.id for node in hierarchy if node.level == level and not node.existing
        })
        counts[f"reused_{level}_nodes"] = len({node.id for node in hierarchy if node.level == level and node.existing})
    return ResolutionReport(
        baseline_snapshot_id=snapshot_id, evidence=report.model_copy(deep=True),
        decisions=prepared + hierarchy_decisions,
        screening=screening,
        hierarchy=hierarchy, tags=resolved_tags,
        mappings=sorted(mappings, key=lambda mapping: _source_key(mapping.source)), links=resolved_links,
        dispositions=[dispositions[key] for key in sorted(dispositions)], counts=counts,
        partial=(
            not report.complete or bool(sum(report.omitted_counts.values()))
            or screening.semantic_status == "failed"
            or any(item.status in {"pending", "deferred"} for item in dispositions.values())
            or any(item.status == "accepted" and item.unapproved_paths for item in dispositions.values())
        ),
    )
