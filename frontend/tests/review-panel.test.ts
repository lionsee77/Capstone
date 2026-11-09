import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ReviewPanel } from "@/app/rounds/[roundId]/review-panel";
import { reviewQueueSchema, roundStateSchema } from "@/lib/api/schemas";

const state = vi.hoisted(() => ({ queue: {} as unknown }));
vi.mock("@/lib/api/queries", () => ({
  useReviewQueue: () => ({ data: state.queue, isPending: false, error: null }),
  useSaveDecisions: () => ({ isPending: false }),
  useApplyDecisions: () => ({ isPending: false }),
  useIntegrateRound: () => ({ isPending: false }),
  useDraftDecisions: () => ({ isPending: false }),
}));

const round = roundStateSchema.parse({
  round_id: "r1", baseline_snapshot: "base", created_at: "2026-11-09T00:00:00Z", source_name: "demo",
  settings: { judge: false, case_b: false, threshold: null, semantic_threshold: null,
    add_candidate_lexical_threshold: null, add_candidate_semantic_threshold: null },
  stages: [], warnings: [], import_counts: {}, merge_counts: {}, pending_sources: 0,
  decisions_applied: true, reviewer: "Reviewer", score_run_id: null, enriched_run_id: null,
  status: "ready_to_integrate",
});
const queue = {
  tags: [], total: 0, offset: 0, limit: 50, undecided: 0, decided: 0, proposals_undecided: 2,
  proposals: ["Marine", "Retail"].map((category) => ({
    proposal_id: category, level: "speciality", name: "Navigation", path: ["Industry", category, "Navigation"],
    status: "new", reason: "No baseline match", blocks_creation: false, source_tags: 1, matches: [], decision: null,
  })),
};

describe("repeated names in the round review panel", () => {
  it("renders the shared cluster and affected paths even when integration is available", () => {
    state.queue = reviewQueueSchema.parse({ ...queue, repeated_names: [{
      code: "warning.repeated_speciality_name", name: "navigation", proposal_ids: ["Marine", "Retail"],
      message: "Navigation appears under Marine and Retail. Advisory only; approvals are unchanged.",
    }] });
    const html = renderToStaticMarkup(createElement(ReviewPanel, { round }));
    expect(html).toContain("Navigation appears under Marine and Retail");
    expect(html.match(/See the parent groups above/g)).toHaveLength(2);
    expect(html).toContain("Generate enriched snapshot");
    expect(html).toContain("Create new");
  });

  it("keeps older queues readable and removes collision notices after the cluster clears", () => {
    state.queue = reviewQueueSchema.parse(queue);
    expect(reviewQueueSchema.parse(queue).repeated_names).toEqual([]);
    const html = renderToStaticMarkup(createElement(ReviewPanel, { round }));
    expect(html).not.toContain("Repeated name:");
    expect(html).not.toContain("See the parent groups above");
  });
});
