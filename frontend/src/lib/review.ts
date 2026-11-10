/**
 * Turning a reviewer's choice into a `ReviewDecision`. The API and `merge-preview` check every decision;
 * this only builds the record and fills in a plain reason when the reviewer doesn't write one.
 */
import type { ReviewQueueProposal, ReviewQueueTag, RoundState } from "@/lib/api/schemas";

export type TagAction = "create" | "reuse" | "exclude" | "defer";
export type ProposalAction = "create" | "reuse" | "exclude" | "defer";

export type DecisionInput = {
  members?: { source_dataset: string; tag_id: string }[];
  proposal_id?: string;
  action: TagAction;
  reason: string;
  existing_tag_id?: number;
  existing_concept_id?: number | string;
  approved_paths?: { service: string; category: string; speciality: string }[];
};

export function placementLabel(place: { service: string; category: string; speciality: string }): string {
  return `${place.service} › ${place.category} › ${place.speciality}`;
}

/** The baseline tags a held tag could be mapped to, without repeats. */
export function reuseCandidates(tag: ReviewQueueTag): { tag_id: number; name: string; score: number }[] {
  const found = new Map<number, { tag_id: number; name: string; score: number }>();
  for (const match of tag.closest) {
    if (match.tag_id === null || found.has(match.tag_id)) continue;
    found.set(match.tag_id, { tag_id: match.tag_id, name: match.name ?? `Tag ${match.tag_id}`, score: match.score });
  }
  return [...found.values()];
}

/** A tag decision. Returns null when the choice is incomplete: nowhere to place it, or nothing to reuse. */
export function tagDecision(
  tag: ReviewQueueTag, action: TagAction, options: { placement?: number; reuseTagId?: number; reason?: string } = {},
): DecisionInput | null {
  const members = [tag.source];
  const written = options.reason?.trim();
  if (action === "exclude") return { members, action, reason: written || "Reviewed: not wanted in the taxonomy" };
  if (action === "defer") return { members, action, reason: written || "Reviewed: needs more information" };
  const place = tag.placements[options.placement ?? 0];
  if (!place) return null;
  const approved_paths = [{ service: place.service, category: place.category, speciality: place.speciality }];
  if (action === "create") {
    return { members, action, approved_paths, reason: written || `Reviewed: placed under ${placementLabel(place)}` };
  }
  const target = reuseCandidates(tag).find((candidate) => candidate.tag_id === options.reuseTagId);
  if (!target) return null;
  return {
    members, action, approved_paths, existing_tag_id: target.tag_id,
    reason: written || `Reviewed: same concept as existing tag '${target.name}'`,
  };
}

// Identify pending review work without treating advisory or already decided tags as bulk targets.
export function tagNeedsDecision(tag: ReviewQueueTag): boolean {
  return tag.status === "pending" && tag.decision === null;
}

/** Apply a bulk choice only to pending, undecided tags, even if the selection contains stale context rows. */
export function bulkTagDecisions(tags: ReviewQueueTag[], action: Exclude<TagAction, "reuse">, reason?: string): DecisionInput[] {
  return tags.filter(tagNeedsDecision).flatMap((tag) => tagDecision(tag, action, { reason }) ?? []);
}

/**
 * What a proposal can actually become: the same level, under its own parent. The API refuses anything else.
 * A child can become eligible once its parent is reused; the queue is rebuilt then, so it is offered at that point.
 */
export function reusableMatches(proposal: ReviewQueueProposal): ReviewQueueProposal["matches"] {
  return proposal.matches.filter((match) => match.reusable && match.same_parent && match.id !== null && match.level === proposal.level);
}

/** A hierarchy proposal's decision; a tag's fields aren't allowed on one, so it is built separately. */
export function proposalDecision(
  proposal: ReviewQueueProposal, action: ProposalAction, options: { conceptId?: number | string; reason?: string } = {},
): DecisionInput | null {
  const written = options.reason?.trim();
  const base = { proposal_id: proposal.proposal_id, action };
  if (action === "reuse") {
    const target = reusableMatches(proposal).find((match) => match.id === options.conceptId);
    if (!target || target.id === null) return null;
    return { ...base, existing_concept_id: target.id, reason: written || `Reviewed: same as existing '${target.name}'` };
  }
  const defaults = {
    create: "Reviewed: a new concept",
    exclude: "Reviewed: not wanted in the taxonomy",
    defer: "Reviewed: needs more information",
  };
  return { ...base, reason: written || defaults[action] };
}

/** The dashboard's comparison of the taxonomy before and after this round, once both are scored. */
export function roundCompareHref(round: RoundState): string | null {
  if (!round.score_run_id || !round.enriched_run_id) return null;
  const params = new URLSearchParams({
    before: round.score_run_id, before_subject: "round_baseline",
    after: round.enriched_run_id, after_subject: "enriched",
  });
  return `/compare?${params}`;
}

/** The offset to show after the queue shrinks: the last page that still has tags, rather than an empty one. */
export function clampOffset(offset: number, total: number, pageSize: number): number {
  if (offset < total) return offset;
  return total === 0 ? 0 : Math.floor((total - 1) / pageSize) * pageSize;
}

/** Whether the round has been through a merge preview, so a review queue can exist. */
export function hasReviewQueue(round: RoundState): boolean {
  if (!["review", "ready_to_integrate", "failed"].includes(round.status)) return false;
  const merge = round.stages.find((stage) => stage.id === "merge");
  return merge?.status === "done" || (merge?.status === "failed" && !round.decisions_applied);
}
