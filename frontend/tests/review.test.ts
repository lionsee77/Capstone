import { describe, expect, it } from "vitest";

import type { ReviewQueueProposal, ReviewQueueTag, RoundState } from "@/lib/api/schemas";
import {
  actionLabel, bulkTagDecisions, canReuse, chosenReuseTag, clampOffset, closestMatchLabel, hasReviewQueue, heldExplanation, placementLabel, proposalDecision, reasonLabels, reusableMatches, reuseCandidateLabel, reuseCandidates, reviewNextStep, reviewProgress, reviewTab, roundCompareHref, savedMessage, stagesWorthShowing, tagDecision,
} from "@/lib/review";

const place = (category: string) => ({ service: "Function", category, speciality: "Analytics", confidence: 0.6, reason: "Fits" });
const tag = (overrides: Partial<ReviewQueueTag> = {}): ReviewQueueTag => ({
  source: { source_dataset: "onet", tag_id: "7" },
  tag: "Adobe Acrobat", status: "pending", reason: "Held", reason_codes: [],
  placements: [place("First"), place("Second")], closest: [], duplicates: [], decision: null,
  ...overrides,
});
const proposal = (overrides: Partial<ReviewQueueProposal> = {}): ReviewQueueProposal => ({
  proposal_id: "p1", level: "speciality", name: "Tax", path: ["Function", "Finance", "Tax"], status: "review",
  reason: "Exact match elsewhere", blocks_creation: true, source_tags: 2, create_blocked_reason: null, decision: null,
  matches: [{
    id: 5, name: "Tax", level: "speciality", path: ["Function", "Finance", "Tax"], reusable: true, same_parent: true,
    match_types: ["normalized_name"], lexical_score: 1, semantic_score: null,
  }],
  ...overrides,
});
const match = (name: string, tagId: number | null, score = 0.8) => ({ score, basis: "semantic", name, level: "tags", tag_id: tagId });
const stage = (id: string, status: string) => ({ id, status, started_at: null, finished_at: null, summary: null, error: null });
const round = (overrides: Partial<RoundState>): RoundState => ({ score_run_id: null, enriched_run_id: null, stages: [], decisions_applied: true, status: "review", ...overrides }) as RoundState;

describe("tagDecision", () => {
  it("creates at the chosen placement, with a plain reason when none is written", () => {
    expect(tagDecision(tag(), "create", { placement: 1 })).toEqual({
      members: [{ source_dataset: "onet", tag_id: "7" }], action: "create",
      approved_paths: [{ service: "Function", category: "Second", speciality: "Analytics" }],
      reason: "Reviewed: placed under Function › Second › Analytics",
    });
  });

  it("keeps a reviewer's own reason, trimmed", () => {
    expect(tagDecision(tag(), "create", { reason: "  Standard tool " })?.reason).toBe("Standard tool");
    expect(tagDecision(tag(), "create", { reason: "   " })?.reason).toContain("Reviewed:");
  });

  it("reuses a listed existing tag at the chosen placement", () => {
    const decision = tagDecision(tag({ closest: [match("Acrobat", 9)] }), "reuse", { reuseTagId: 9 });
    expect(decision).toMatchObject({ action: "reuse", existing_tag_id: 9 });
    expect(decision?.approved_paths).toHaveLength(1);
  });

  it("gives nothing when the choice is incomplete", () => {
    expect(tagDecision(tag({ placements: [] }), "create")).toBeNull();
    expect(tagDecision(tag(), "reuse", { reuseTagId: 9 })).toBeNull();
    expect(tagDecision(tag({ closest: [match("Acrobat", 9)] }), "reuse")).toBeNull();
  });

  it("carries no placement on an exclude or a defer, which the contract forbids", () => {
    expect(tagDecision(tag(), "exclude")).not.toHaveProperty("approved_paths");
    expect(tagDecision(tag({ placements: [] }), "defer")).toMatchObject({ action: "defer" });
  });
});

describe("reuseCandidates", () => {
  it("lists only matches that are one baseline tag, once each", () => {
    const candidates = reuseCandidates(tag({ closest: [match("A", 1, 0.9), match("A", 1, 0.7), match("B", null)] }));
    expect(candidates).toEqual([{ tag_id: 1, name: "A", score: 0.9, basis: "semantic" }]);
  });

  it("says how each one matched, so a close spelling isn't mistaken for the same concept", () => {
    const [meaning, spelling] = reuseCandidates(tag({
      closest: [match("aws", 1, 0.67), { ...match("cash", 2, 0.75), basis: "lexical" }],
    }));
    expect(reuseCandidateLabel(meaning)).toBe("aws (similar meaning, 0.67)");
    expect(reuseCandidateLabel(spelling)).toBe("cash (similar spelling, 0.75)");
  });
});

describe("choosing a tag to reuse", () => {
  const withCandidates = tag({ closest: [match("Excel", 3)] });

  it("starts with nothing chosen, so Reuse existing is disabled", () => {
    expect(chosenReuseTag("")).toBeUndefined();
    expect(canReuse(withCandidates, undefined)).toBe(false);
  });

  it("enables Reuse existing once a tag is chosen, and disables it again when the choice is cleared", () => {
    const chosen = chosenReuseTag("3");
    expect(chosen).toBe(3);
    expect(canReuse(withCandidates, chosen)).toBe(true);
    expect(canReuse(withCandidates, chosenReuseTag(""))).toBe(false);
  });

  it("stays disabled without a placement, or for a tag that isn't a candidate", () => {
    expect(canReuse(tag({ closest: [match("Excel", 3)], placements: [] }), 3)).toBe(false);
    expect(canReuse(withCandidates, 99)).toBe(false);
  });
});

describe("bulkTagDecisions", () => {
  it("applies one choice across a selection at each tag's best placement", () => {
    const decisions = bulkTagDecisions([tag(), tag({ source: { source_dataset: "onet", tag_id: "8" } })], "create", "Tools");
    expect(decisions.map((decision) => decision.approved_paths?.[0].category)).toEqual(["First", "First"]);
    expect(decisions.every((decision) => decision.reason === "Tools")).toBe(true);
  });

  it("leaves out a tag with nowhere to place it rather than failing the rest", () => {
    expect(bulkTagDecisions([tag({ placements: [] }), tag()], "create")).toHaveLength(1);
    expect(bulkTagDecisions([tag({ placements: [] })], "defer")).toHaveLength(1);
  });
});

describe("proposalDecision", () => {
  it("targets the proposal only, since a tag's fields aren't allowed on it", () => {
    expect(proposalDecision(proposal(), "create")).toEqual({ proposal_id: "p1", action: "create", reason: "Reviewed: a new concept" });
  });

  it("reuses only a reusable match", () => {
    expect(proposalDecision(proposal(), "reuse", { conceptId: 5 })).toMatchObject({ existing_concept_id: 5 });
    expect(proposalDecision(proposal(), "reuse", { conceptId: 99 })).toBeNull();
    const broken = proposal({ matches: [{ ...proposal().matches[0], reusable: false }] });
    expect(proposalDecision(broken, "reuse", { conceptId: 5 })).toBeNull();
  });
});

describe("reusableMatches", () => {
  const candidate = (overrides: object) => ({ ...proposal().matches[0], ...overrides });

  it("offers only what the API would accept: same level, under its own parent", () => {
    const usable = candidate({ id: 1 });
    const offered = reusableMatches(proposal({ matches: [
      usable, candidate({ id: 2, same_parent: false }), candidate({ id: 3, level: "category" }),
      candidate({ id: 4, reusable: false }), candidate({ id: null }),
    ] }));
    expect(offered).toEqual([usable]);
  });

  it("refuses a decision on a match it wouldn't offer", () => {
    const elsewhere = proposal({ matches: [candidate({ id: 2, same_parent: false })] });
    expect(proposalDecision(elsewhere, "reuse", { conceptId: 2 })).toBeNull();
  });
});

describe("clampOffset", () => {
  it("keeps a page that still has tags", () => {
    expect(clampOffset(25, 50, 25)).toBe(25);
    expect(clampOffset(0, 0, 25)).toBe(0);
  });

  it("moves back to the last page with tags once a later one empties", () => {
    expect(clampOffset(25, 25, 25)).toBe(0);
    expect(clampOffset(50, 30, 25)).toBe(25);
    expect(clampOffset(25, 0, 25)).toBe(0);
  });
});

describe("round links and panels", () => {
  it("compares the round's baseline with its snapshot within the run that scored both", () => {
    expect(roundCompareHref(round({ score_run_id: "a" }))).toBeNull();
    expect(roundCompareHref(round({ score_run_id: "a", enriched_run_id: "b" })))
      .toBe("/compare?before=b&before_subject=round_baseline&after=b&after_subject=enriched");
  });

  it("offers review once a preview exists, and after one refused a decision", () => {
    const merged = [stage("merge", "done")] as RoundState["stages"];
    expect(hasReviewQueue(round({ status: "review", stages: merged }))).toBe(true);
    const refused = [stage("merge", "failed")] as RoundState["stages"];
    expect(hasReviewQueue(round({ status: "failed", stages: refused, decisions_applied: false }))).toBe(true);
    expect(hasReviewQueue(round({ status: "failed", stages: refused, decisions_applied: true }))).toBe(false);
    expect(hasReviewQueue(round({ status: "complete", stages: merged }))).toBe(false);
  });

  it("describes a placement in reading order", () => {
    expect(placementLabel(place("Finance"))).toBe("Function › Finance › Analytics");
  });
});

describe("the review page's wording", () => {
  it("says the API's actions in a reviewer's words, and passes on anything unknown", () => {
    expect(actionLabel("create")).toBe("Add as new");
    expect(actionLabel("exclude")).toBe("Leave out");
    expect(actionLabel("something_new")).toBe("something_new");
  });

  it("turns reason codes into short plain labels without repeats", () => {
    expect(reasonLabels(tag({ reason_codes: ["producer_flagged", "hierarchy_review", "producer_flagged"] }))).toEqual([
      "Unsure where it goes", "New category or speciality",
    ]);
  });

  it("says why a decision was held, and what to do about it", () => {
    expect(heldExplanation(tag({ reason_codes: ["hierarchy_blocked"] }))).toContain("left out or isn't settled");
    expect(heldExplanation(tag({ reason_codes: ["producer_flagged"] }))).toContain("couldn't be used");
  });

  it("says how a closest match matched", () => {
    expect(closestMatchLabel(match("Excel", 3))).toBe("Excel (tags, similar meaning, 0.80)");
    expect(closestMatchLabel({ score: 0.9, basis: "lexical", name: null, level: null, tag_id: null })).toBe("unnamed (unknown level, similar spelling, 0.90)");
  });

  it("explains what happened to a tag that just left the list", () => {
    const decision = tagDecision(tag(), "exclude")!;
    expect(savedMessage([decision], 0)).toContain("Saved: Leave out");
    expect(savedMessage([decision, decision], 0)).toContain("Saved 2 decisions");
    expect(savedMessage([], 1)).toContain("back in the list");
    expect(savedMessage([], 0)).toBe("");
  });
});

describe("review progress", () => {
  it("doesn't count a decision that couldn't be used as settled", () => {
    expect(reviewProgress({ undecided: 5, decided: 5, held: 1 })).toEqual({ done: 4, total: 10, percent: 40 });
  });

  it("treats an empty queue as finished", () => {
    expect(reviewProgress({ undecided: 0, decided: 0, held: 0 }).percent).toBe(100);
  });

  it("names the one next step", () => {
    const queue = { undecided: 0, proposals_undecided: 0 };
    expect(reviewNextStep(round({ status: "running" }), queue)).toBe("working");
    expect(reviewNextStep(round({ decisions_applied: false }), queue)).toBe("apply");
    expect(reviewNextStep(round({ pending_sources: 3 }), queue)).toBe("decide");
    expect(reviewNextStep(round({ pending_sources: 0 }), { ...queue, proposals_undecided: 1 })).toBe("decide");
    expect(reviewNextStep(round({ pending_sources: 0 }), queue)).toBe("generate");
  });

  it("starts on the groups until they're settled, and respects a list someone picked", () => {
    expect(reviewTab(null, { proposals_undecided: 2 })).toBe("proposals");
    expect(reviewTab(null, { proposals_undecided: 0 })).toBe("undecided");
    expect(reviewTab(null, undefined)).toBe("undecided");
    expect(reviewTab("decided", { proposals_undecided: 2 })).toBe("decided");
  });

  it("folds the stages once they've finished their part", () => {
    expect(stagesWorthShowing({ status: "review" })).toBe(false);
    expect(stagesWorthShowing({ status: "failed" })).toBe(true);
    expect(stagesWorthShowing({ status: "running" })).toBe(true);
  });
});
