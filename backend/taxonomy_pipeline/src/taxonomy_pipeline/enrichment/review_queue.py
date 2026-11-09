"""What a reviewer needs in one place: every held tag and hierarchy proposal, with the evidence for each.

The evidence is scattered across a merge run: why a tag is held (`dispositions`), where it could go
(`evidence.tags`), what it resembles in the baseline (the saturation report), and which incoming tags it may
duplicate (`evidence.candidates`). `build_review_queue` joins them once, after each merge preview, so the
queue can be paged without re-reading those large reports for every request.

This is evidence only. Decisions stay `ReviewDecision`s applied through `merge-preview`, so a decision made
here is checked and recorded exactly like one in a hand-edited file.
"""
from __future__ import annotations

from collections import defaultdict

from ..matching.names import normalize
from ..taxonomy.base import TaxonomyDataset
from .bulk_decisions import closest_matches
from .models import (
    ResolutionReport,
    ReviewQueueDuplicate,
    ReviewQueueEvidence,
    ReviewQueueMatch,
    ReviewQueuePlacement,
    ReviewQueueProposal,
    ReviewQueueProposalMatch,
    ReviewQueueTag,
    TagProposal,
)
from .proposals.models import ProposalScreeningReport
from .repeated_names import live_placements
from .resolution import resolve_outputs
from .routing import SaturationReport


# Join shared effective screening with tag evidence for every reviewer-facing surface.
def build_review_queue(
    resolution: ResolutionReport, saturation: SaturationReport | None, baseline: TaxonomyDataset,
    *, decided_sources: set[tuple[str, str]] = frozenset(), decided_proposals: set[str] = frozenset(),
) -> ReviewQueueEvidence:
    """Tags still held or dealt with by a reviewer, and proposals that need or had a decision.

    Automatically accepted tags appear only when a live path carries an advisory collision.
    """
    effective = resolution.effective_screening
    if effective is None and resolution.screening is not None:
        # Older saved runs have only raw screening; replay once to rebuild the derived review evidence.
        effective = resolve_outputs(
            resolution.evidence, baseline, resolution.decisions, screening=resolution.screening,
        ).effective_screening
    warned_sources = set()
    if effective is not None:
        warned_ids = {identifier for warning in effective.repeated_names for identifier in warning.proposal_ids}
        warned_paths = {tuple(item.path) for item in effective.proposals if item.proposal_id in warned_ids}
        warned_sources = {
            (source, tag_id) for source, tag_id, path in live_placements(
                resolution.evidence, effective, resolution.decisions,
            ) if path in warned_paths or path[:2] in warned_paths
        }
    tags = {(tag.source.source_dataset, tag.source.tag_id): tag for tag in resolution.evidence.tags}
    tag_ids = _unique_tag_ids(baseline)
    matches = saturation.ambiguous_matches() if saturation else {}
    duplicates = _duplicates(resolution, tags)
    items = []
    for disposition in resolution.dispositions:
        key = (disposition.source.source_dataset, disposition.source.tag_id)
        if disposition.status == "accepted" and key not in decided_sources and key not in warned_sources:
            continue
        tag = tags.get(key)
        concept = matches.get(key[1]) if saturation and key[0] == saturation.incoming else None
        items.append(ReviewQueueTag(
            source=disposition.source, tag=tag.tag if tag else key[1], status=disposition.status,
            reason=disposition.reason, reason_codes=disposition.reason_codes,
            placements=_placements(tag) if tag else [],
            closest=[
                ReviewQueueMatch(
                    score=round(match.score, 4), basis=match.basis, name=match.name, level=match.level,
                    tag_id=tag_ids.get(normalize(match.name)) if match.level == "tags" and match.name else None,
                )
                for match in (closest_matches(concept) if concept else [])
            ],
            duplicates=duplicates.get(key, []),
        ))
    return ReviewQueueEvidence(
        tags=sorted(items, key=lambda item: (normalize(item.tag), item.source.tag_id)),
        proposals=_proposals(effective, decided_proposals),
        repeated_names=effective.repeated_names if effective else [],
    )


def _unique_tag_ids(baseline: TaxonomyDataset) -> dict[str, int]:
    """Baseline tag IDs by name, leaving out a name several rows share: reusing it would be a guess."""
    ids: dict[str, list[int]] = defaultdict(list)
    for row in baseline.tags:
        if isinstance(row.get("id"), int) and row.get("name"):
            ids[normalize(str(row["name"]))].append(row["id"])
    return {name: found[0] for name, found in ids.items() if len(found) == 1}


def _placements(tag: TagProposal) -> list[ReviewQueuePlacement]:
    """Every distinct path proposed for the tag, most confident first, then in name order so reruns agree."""
    best: dict[tuple[str, str, str], ReviewQueuePlacement] = {}
    for row in tag.placements:
        if row.category is None:
            continue
        key = (row.service, row.category, row.speciality)
        if key not in best or (row.confidence or 0) > (best[key].confidence or 0):
            best[key] = ReviewQueuePlacement(
                service=row.service, category=row.category, speciality=row.speciality,
                confidence=row.confidence, reason=row.reason,
            )
    return sorted(best.values(), key=lambda p: (-(p.confidence or 0), p.service, p.category, p.speciality))


def _duplicates(
    resolution: ResolutionReport, tags: dict[tuple[str, str], TagProposal],
) -> dict[tuple[str, str], list[ReviewQueueDuplicate]]:
    found: dict[tuple[str, str], list[ReviewQueueDuplicate]] = defaultdict(list)
    for pair in resolution.evidence.candidates:
        left, right = (pair.left.source_dataset, pair.left.tag_id), (pair.right.source_dataset, pair.right.tag_id)
        for key, other, ref in ((left, right, pair.right), (right, left, pair.left)):
            tag = tags.get(other)
            found[key].append(ReviewQueueDuplicate(
                source=ref, tag=tag.tag if tag else ref.tag_id, reason=pair.reason, similarity=pair.similarity,
            ))
    return found


# Include advisory collisions even when screening has accepted their proposals automatically.
def _proposals(
    effective: ProposalScreeningReport | None, decided: set[str],
) -> list[ReviewQueueProposal]:
    """Held, warned, or previously decided proposals, without turning advisories into review holds."""
    if effective is None:
        return []
    warned = {identifier for warning in effective.repeated_names for identifier in warning.proposal_ids}
    visible = decided | warned
    items = []
    for proposal in effective.proposals:
        if proposal.status != "review" and proposal.proposal_id not in visible:
            continue
        items.append(ReviewQueueProposal(
            proposal_id=proposal.proposal_id, level=proposal.level, name=proposal.name, path=proposal.path,
            status=proposal.status, reason=proposal.reason, blocks_creation=proposal.blocks_creation,
            source_tags=len(proposal.source_tags),
            matches=[
                ReviewQueueProposalMatch(
                    id=match.concept.id, name=match.concept.name, level=match.concept.level,
                    path=[step for step in match.concept.path if step is not None],
                    reusable=match.concept.reusable, same_parent=match.same_parent, match_types=match.match_types,
                    lexical_score=match.lexical_score, semantic_score=match.semantic_score,
                )
                for match in proposal.matches
            ],
        ))
    return sorted(items, key=lambda item: (item.level, item.path))
