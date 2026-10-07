"""Draft review decisions in bulk for tags saturation couldn't confirm as new.

Routing every `REVIEW_AMBIGUOUS` tag (see `routing.py`) holds each one in
merge-preview for a human decision, which on SkillsFuture is well over a
thousand tags. Many are only loosely similar to anything in the baseline, and
some are close enough to be an existing tag. This module turns two thresholds on
saturation's closest-match similarity into decisions for both ends, and leaves
the middle band pending for a person:

- similarity below `create_below`: `create` the tag at its highest-confidence path;
- closest match is a baseline *tag* at or above `reuse_at_or_above`: `reuse` it,
  linked at the same path.

The result is a draft decisions file, not an approval: it is fed back through
`merge-preview --decisions` like a hand-written one, so every bulk decision
appears in `preview.csv` with its reason and can be edited or removed. Tags held
for another reason (a duplicate candidate pair, a baseline name match) or that
already have a decision are never touched.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..datasets.base import TaxonomyDataset
from ..metrics.integrity import normalize
from .models import ClassificationOutput, ResolutionReport, ReviewDecision, SourceTagRef, TaxonomyPath
from .routing import SaturationReport

# The pending reason resolution gives a tag held only because its producer flagged it.
PRODUCER_REVIEW_PREFIX = "Producer requested review"


@dataclass
class BulkDraft:
    """Decisions to write (earlier ones first, then the drafted ones) and why each pending tag was or wasn't decided."""

    decisions: list[ReviewDecision]
    drafted: list[ReviewDecision]
    counts: Counter[str] = field(default_factory=Counter)


def closest_similarity(concept: dict[str, Any]) -> tuple[float, str, str | None, str | None] | None:
    """Saturation's closest match on a 0-1 scale as (score, basis, name, level).

    Meaning is used when the report has it, else spelling (RapidFuzz 0-100, rescaled).
    """
    if concept.get("closest_semantic_score") is not None:
        return (float(concept["closest_semantic_score"]), "semantic",
                concept.get("closest_semantic_baseline_name"), concept.get("closest_semantic_baseline_level"))
    if concept.get("closest_baseline_score") is not None:
        return (float(concept["closest_baseline_score"]) / 100, "lexical",
                concept.get("closest_baseline_name"), concept.get("closest_baseline_level"))
    return None


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
    if create_below is not None and reuse_at_or_above is not None and create_below > reuse_at_or_above:
        raise ValueError("create_below must not be above reuse_at_or_above")
    existing = list(existing or [])
    decided = {(member.source_dataset, member.tag_id) for decision in existing for member in decision.members}
    paired = {(ref.source_dataset, ref.tag_id) for pair in resolution.evidence.candidates
              for ref in (pair.left, pair.right)}
    placements = {(tag.source.source_dataset, tag.source.tag_id): tag.placements for tag in resolution.evidence.tags}
    ambiguous = saturation.ambiguous_matches()

    draft = BulkDraft(decisions=existing, drafted=[])
    for disposition in resolution.dispositions:
        if disposition.status != "pending":
            continue
        key = (disposition.source.source_dataset, disposition.source.tag_id)
        concept = ambiguous.get(key[1]) if key[0] == saturation.incoming else None
        closest = closest_similarity(concept) if concept else None
        path = _top_path(placements.get(key, []))
        if key in decided:
            draft.counts["already_decided"] += 1
            continue
        if concept is None:
            draft.counts["not_ambiguous"] += 1
            continue
        if key in paired or not disposition.reason.startswith(PRODUCER_REVIEW_PREFIX):
            draft.counts["held_for_another_reason"] += 1
            continue
        if closest is None or path is None:
            draft.counts["no_score_or_path"] += 1
            continue
        score, basis, name, level = closest
        members = [SourceTagRef(source_dataset=key[0], tag_id=key[1])]
        tag_id = _baseline_tag_id(baseline, name) if level == "tags" else None
        if reuse_at_or_above is not None and score >= reuse_at_or_above and tag_id is not None:
            decision = ReviewDecision(
                members=members, action="reuse", existing_tag_id=tag_id, approved_paths=[path],
                reason=f"Bulk: {basis} similarity {score:.2f} to existing tag '{name}' is at or above "
                       f"{reuse_at_or_above}; reused at its highest-confidence placement.",
            )
            draft.counts["reuse"] += 1
        elif create_below is not None and score < create_below:
            decision = ReviewDecision(
                members=members, action="create", approved_paths=[path],
                reason=f"Bulk: {basis} similarity {score:.2f} to '{name}' is below {create_below}; "
                       "created at its highest-confidence placement.",
            )
            draft.counts["create"] += 1
        else:
            draft.counts["left_for_review"] += 1
            continue
        draft.drafted.append(decision)
    draft.decisions = existing + draft.drafted
    return draft
