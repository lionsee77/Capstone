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

export type ReuseCandidate = { tag_id: number; name: string; score: number; basis: string };

/** The baseline tags a held tag could be mapped to, without repeats. None is ever chosen for the reviewer. */
export function reuseCandidates(tag: ReviewQueueTag): ReuseCandidate[] {
  const found = new Map<number, ReuseCandidate>();
  for (const match of tag.closest) {
    if (match.tag_id === null || found.has(match.tag_id)) continue;
    found.set(match.tag_id, {
      tag_id: match.tag_id, name: match.name ?? `Tag ${match.tag_id}`, score: match.score, basis: match.basis,
    });
  }
  return [...found.values()];
}

/** Says how a candidate matched, because a close spelling alone says nothing about meaning (Bash and cash). */
export function reuseCandidateLabel(candidate: ReuseCandidate): string {
  const how = candidate.basis === "semantic" ? "similar meaning" : "similar spelling";
  return `${candidate.name} (${how}, ${candidate.score.toFixed(2)})`;
}

/** The reuse dropdown's value as a tag ID. "" is its "Choose a tag" placeholder, so it means nothing is chosen. */
export function chosenReuseTag(value: string): number | undefined {
  return value === "" ? undefined : Number(value);
}

/** Whether "Reuse existing" can be pressed: only once the reviewer has chosen a tag, and there's a placement for it. */
export function canReuse(tag: ReviewQueueTag, reuseTagId: number | undefined): boolean {
  return reuseTagId !== undefined && tag.placements.length > 0 && reuseCandidates(tag).some((item) => item.tag_id === reuseTagId);
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

/** The same choice across a selection, each tag at its most confident placement. Tags it can't apply to are left out. */
export function bulkTagDecisions(tags: ReviewQueueTag[], action: Exclude<TagAction, "reuse">, reason?: string): DecisionInput[] {
  return tags.flatMap((tag) => tagDecision(tag, action, { reason }) ?? []);
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

/** The dashboard's comparison of the taxonomy before and after this round: one run scored both. */
export function roundCompareHref(round: RoundState): string | null {
  if (!round.enriched_run_id) return null;
  const params = new URLSearchParams({
    before: round.enriched_run_id, before_subject: "round_baseline",
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
