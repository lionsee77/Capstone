"""Draft review decisions in bulk for tags saturation couldn't confirm as new.

Routing every `REVIEW_AMBIGUOUS` tag (see `routing.py`) holds each one in
merge-preview for a human decision, which on SkillsFuture is well over a
thousand tags. Many are only loosely similar to anything in the baseline, and
some are close enough to be an existing tag. This module turns two thresholds on
saturation's closest-match similarity into decisions for both ends, and leaves
the middle band pending for a person:

- every closest match (meaning and spelling alike) below `create_below`: `create`
  the tag at its highest-confidence path. One strong score of either kind is
  enough to keep a tag for review;
- the decisive closest match (meaning when the report has it, since a close
  spelling alone is unconfirmed, else spelling) is one baseline *tag* at or above
  `reuse_at_or_above`: `reuse` it, linked at the same path.

The result is a draft decisions file, not an approval: it is fed back through
`merge-preview --decisions` like a hand-written one, so every bulk decision
appears in `preview.csv` with its reason and can be edited or removed. Tags held
for another reason (a duplicate candidate pair, a baseline name match) or that
already have a decision are never touched.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from ..datasets.base import TaxonomyDataset
from ..metrics.integrity import normalize
from .models import ClassificationOutput, ResolutionReport, ReviewDecision, SourceTagRef, TaxonomyPath
from .resolution import taxonomy_snapshot_id
from .review import apply_hierarchy_decisions
from .routing import SaturationReport

# The pending reason resolution gives a tag held only because its producer flagged it.
PRODUCER_REVIEW_PREFIX = "Producer requested review"


@dataclass
class BulkDraft:
    """Decisions to write (earlier ones first, then the drafted ones) and why each pending tag was or wasn't decided."""

    decisions: list[ReviewDecision]
    drafted: list[ReviewDecision]
    counts: Counter[str] = field(default_factory=Counter)


class ClosestMatch(NamedTuple):
    """One of saturation's closest baseline matches, scored 0-1."""

    score: float
    basis: str
    name: str | None
    level: str | None


def closest_matches(concept: dict[str, Any]) -> list[ClosestMatch]:
    """Saturation's closest matches by meaning, then spelling (RapidFuzz 0-100, rescaled), when present."""
    matches = []
    if concept.get("closest_semantic_score") is not None:
        matches.append(ClosestMatch(float(concept["closest_semantic_score"]), "semantic",
                                    concept.get("closest_semantic_baseline_name"),
                                    concept.get("closest_semantic_baseline_level")))
    if concept.get("closest_baseline_score") is not None:
        matches.append(ClosestMatch(float(concept["closest_baseline_score"]) / 100, "lexical",
                                    concept.get("closest_baseline_name"), concept.get("closest_baseline_level")))
    return matches


def _check_threshold(name: str, value: float | None) -> None:
    if value is not None and not (math.isfinite(value) and 0 <= value <= 1):
        raise ValueError(f"{name} must be a similarity between 0 and 1, got {value}")


def _top_path(placements: list[ClassificationOutput]) -> TaxonomyPath | None:
    """The highest-confidence placement; ties go to the first path in name order, so reruns agree."""
    placed = [row for row in placements if row.category is not None]
    if not placed:
        return None
    best = min(placed, key=lambda row: (-(row.confidence or 0.0), row.service, row.category, row.speciality))
    return TaxonomyPath(service=best.service, category=best.category, speciality=best.speciality)


def _baseline_tag_id(taxonomy: TaxonomyDataset, name: str | None) -> Any:
    """The one baseline tag with this name, or None when there is none or the name is shared."""
    if not name:
        return None
    matches = [row.get("id") for row in taxonomy.tags
               if isinstance(row.get("name"), str) and normalize(row["name"]) == normalize(name)]
    return matches[0] if len(matches) == 1 else None


def _with_saved(saved: list[ReviewDecision], supplied: list[ReviewDecision]) -> list[ReviewDecision]:
    """`supplied`, then every decision the merge run already applied that it doesn't repeat.

    The draft must carry the run's saved decisions (hierarchy approvals especially, which may be what unlocks
    a drafted tag), or applying it alone would drop them. A supplied decision on the same tag or hierarchy
    proposal as a saved one, but different, is a conflict: say which one wins instead of guessing.
    """
    def targets(decision: ReviewDecision) -> set[tuple[str, ...]]:
        if decision.proposal_id is not None:
            return {("proposal", decision.proposal_id)}
        return {("tag", member.source_dataset, member.tag_id) for member in decision.members}

    supplied_targets = {target: decision for decision in supplied for target in targets(decision)}
    merged = list(supplied)
    for decision in saved:
        if decision in supplied:
            continue
        clash = next((supplied_targets[target] for target in targets(decision) if target in supplied_targets), None)
        if clash is not None:
            raise ValueError(f"--decisions changes a decision this merge run already applied ({clash.reason!r} vs "
                             f"{decision.reason!r}); rerun merge-preview with the decisions you want, then bulk-decide")
        merged.append(decision)
    return merged


def draft_decisions(
    resolution: ResolutionReport,
    saturation: SaturationReport,
    baseline: TaxonomyDataset,
    *,
    create_below: float | None = None,
    reuse_at_or_above: float | None = None,
    existing: list[ReviewDecision] | None = None,
) -> BulkDraft:
    """Draft `create`/`reuse` decisions for pending ambiguous tags outside the middle similarity band."""
    if create_below is None and reuse_at_or_above is None:
        raise ValueError("give create_below, reuse_at_or_above, or both")
    _check_threshold("create_below", create_below)
    _check_threshold("reuse_at_or_above", reuse_at_or_above)
    if create_below is not None and reuse_at_or_above is not None and create_below > reuse_at_or_above:
        raise ValueError("create_below must not be above reuse_at_or_above")
    if taxonomy_snapshot_id(baseline) != resolution.baseline_snapshot_id:
        raise ValueError("The baseline changed since this merge run; rerun merge-preview against the current baseline")
    existing = _with_saved(resolution.decisions, list(existing or []))
    decided = {(member.source_dataset, member.tag_id) for decision in existing for member in decision.members}
    paired = {(ref.source_dataset, ref.tag_id) for pair in resolution.evidence.candidates
              for ref in (pair.left, pair.right)}
    placements = {(tag.source.source_dataset, tag.source.tag_id): tag.placements for tag in resolution.evidence.tags}
    ambiguous = saturation.ambiguous_matches()
    effective = (
        apply_hierarchy_decisions(resolution.screening, baseline, resolution.decisions)
        if resolution.screening else None
    )
    hierarchy_held = {source for proposal in effective.proposals if proposal.status == "review"
                      for source in proposal.source_tags} if effective else set()

    draft = BulkDraft(decisions=existing, drafted=[])
    for disposition in resolution.dispositions:
        if disposition.status != "pending":
            continue
        key = (disposition.source.source_dataset, disposition.source.tag_id)
        concept = ambiguous.get(key[1]) if key[0] == saturation.incoming else None
        closest = closest_matches(concept) if concept else []
        path = _top_path(placements.get(key, []))
        if key in decided:
            draft.counts["already_decided"] += 1
            continue
        if concept is None:
            draft.counts["not_ambiguous"] += 1
            continue
        if key in paired or key in hierarchy_held or not disposition.reason.startswith(PRODUCER_REVIEW_PREFIX):
            draft.counts["held_for_another_reason"] += 1
            continue
        if not closest or path is None:
            draft.counts["no_score_or_path"] += 1
            continue
        members = [SourceTagRef(source_dataset=key[0], tag_id=key[1])]
        decisive, strongest = closest[0], max(closest, key=lambda match: match.score)
        tag_id = _baseline_tag_id(baseline, decisive.name) if decisive.level == "tags" else None
        if reuse_at_or_above is not None and decisive.score >= reuse_at_or_above and tag_id is not None:
            decision = ReviewDecision(
                members=members, action="reuse", existing_tag_id=tag_id, approved_paths=[path],
                reason=f"Bulk: {decisive.basis} similarity {decisive.score:.2f} to existing tag '{decisive.name}' "
                       f"is at or above {reuse_at_or_above}; reused at its highest-confidence placement.",
            )
            draft.counts["reuse"] += 1
        elif create_below is not None and strongest.score < create_below:
            decision = ReviewDecision(
                members=members, action="create", approved_paths=[path],
                reason=f"Bulk: closest match is {strongest.basis} similarity {strongest.score:.2f} to "
                       f"'{strongest.name}', below {create_below}; created at its highest-confidence placement.",
            )
            draft.counts["create"] += 1
        else:
            draft.counts["left_for_review"] += 1
            continue
        draft.drafted.append(decision)
    draft.decisions = existing + draft.drafted
    return draft
