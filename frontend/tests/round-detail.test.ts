import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RoundDetail } from "@/app/rounds/[roundId]/round-detail";
import { reviewQueueSchema, roundStateSchema, type RoundState } from "@/lib/api/schemas";

const state = vi.hoisted(() => ({
  round: undefined as unknown,
  queue: { data: undefined as unknown, isPending: false, error: null as Error | null },
}));
const idle = { isPending: false, mutate: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/queries", () => ({
  useRound: () => ({ data: state.round, isPending: false, error: null }),
  useRunRound: () => idle,
  useRoundConfigText: () => ({ data: undefined, error: null }),
  useDeleteRound: () => idle,
  useReviewQueue: () => state.queue,
  useSaveDecisions: () => idle,
  useDraftDecisions: () => idle,
  useApplyDecisions: () => idle,
  useIntegrateRound: () => idle,
}));

const id = (seed: string) => seed.repeat(64).slice(0, 64);
const acrobat = "Repeated new speciality name 'adobe acrobat' across 2 distinct parents. "
  + `Parent Industry > Engineering: [${id("a")}: Industry > Engineering > Adobe Acrobat]; `
  + `Parent Industry > Media: [${id("b")}: Industry > Media > Adobe Acrobat]. `
  + "Advisory only; placements and approvals are unchanged.";
const unrelated = "soc.csv changed after it was imported. Import the source again before running this round.";

const stage = (id: string, status: string) => ({
  id, status, started_at: null, finished_at: "2026-11-19T00:00:00Z", summary: null, error: null,
});
const round = (status: RoundState["status"], mergeStatus: string): RoundState => roundStateSchema.parse({
  round_id: "onet-demo", baseline_snapshot: "judged-1015", created_at: "2026-11-19T00:00:00Z", source_name: "onet",
  settings: { judge: false, case_b: false, threshold: null, semantic_threshold: null,
    add_candidate_lexical_threshold: null, add_candidate_semantic_threshold: null },
  stages: [stage("score", "done"), stage("merge", mergeStatus)],
  mapping: null, warnings: [`warning.repeated_speciality_name: ${acrobat}`, unrelated], import_counts: {},
  merge_counts: {}, pending_sources: 0, decisions_applied: true, reviewer: null, score_run_id: null,
  enriched_run_id: null, status,
});
const queue = reviewQueueSchema.parse({
  tags: [], total: 0, offset: 0, limit: 50, undecided: 0, decided: 0, held: 0, proposals_undecided: 0, proposals: [],
  repeated_names: [{
    code: "warning.repeated_speciality_name", name: "adobe acrobat", proposal_ids: [id("a"), id("b")], message: acrobat,
  }],
});

const render = () => renderToStaticMarkup(createElement(RoundDetail, { roundId: "onet-demo" }));
const count = (html: string, text: string) => html.split(text).length - 1;

describe("repeated names on the round page", () => {
  beforeEach(() => {
    state.queue = { data: undefined, isPending: false, error: null };
  });

  it("lists them on the page when there is no review panel, and keeps other warnings under Worth checking", () => {
    state.round = round("ready", "pending");
    const html = render();
    expect(count(html, "Repeated names")).toBe(1);
    expect(html).toContain("Adobe Acrobat");
    expect(html).toContain("Worth checking");
    expect(html).toContain(unrelated);
    expect(html).not.toContain("warning.repeated_speciality_name");
  });

  it("shows them once, from the review queue, when the panel has loaded", () => {
    state.round = round("review", "done");
    state.queue = { data: queue, isPending: false, error: null };
    const html = render();
    expect(count(html, "Repeated names")).toBe(1);
    expect(html).toContain("Automatically accepted tags are available under All");
    expect(html).toContain(unrelated);
  });

  it("keeps them from the round's warnings while the queue loads or after it fails", () => {
    state.round = round("review", "done");
    for (const queueState of [
      { data: undefined, isPending: true, error: null },
      { data: undefined, isPending: false, error: new Error("Review queue unavailable") },
    ]) {
      state.queue = queueState;
      const html = render();
      expect(count(html, "Repeated names")).toBe(1);
      expect(html).toContain("Adobe Acrobat");
    }
    expect(render()).toContain("Could not load the review queue");
  });
});
