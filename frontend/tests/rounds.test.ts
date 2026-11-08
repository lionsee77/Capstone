import { describe, expect, it } from "vitest";

import { pollRound, RUN_POLL_INTERVAL_MS } from "@/lib/api/queries";
import { roundStateSchema, type RoundState } from "@/lib/api/schemas";
import { isRoundActive, roundStatusLabel, STAGE_LABELS } from "@/lib/rounds";

const stage = { started_at: null, finished_at: null, summary: null, error: null };
const round = (status: RoundState["status"]): RoundState => roundStateSchema.parse({
  round_id: "r1", baseline_snapshot: "base", created_at: "2026-11-08T00:00:00Z", source_name: "onet",
  settings: {
    judge: false, case_b: false, threshold: null, semantic_threshold: null,
    add_candidate_lexical_threshold: null, add_candidate_semantic_threshold: null,
  },
  stages: [{ id: "score", status: "done", ...stage }],
  warnings: [], import_counts: { tags: 1 }, merge_counts: {}, pending_sources: 0, decisions_applied: true,
  reviewer: null, score_run_id: null, enriched_run_id: null, status,
});

describe("round progress", () => {
  it("polls only while a round runs", () => {
    expect(pollRound(round("running"))).toBe(RUN_POLL_INTERVAL_MS);
    expect(pollRound(round("review"))).toBe(false);
    expect(pollRound(undefined)).toBe(false);
    expect(isRoundActive(round("failed"))).toBe(false);
  });

  it("has words for every status and stage", () => {
    for (const status of ["setup", "ready", "running", "failed", "review", "ready_to_integrate", "complete"] as const) {
      expect(roundStatusLabel(status)).toBeTruthy();
    }
    expect(Object.keys(STAGE_LABELS)).toHaveLength(7);
  });

  it("rejects a status the API doesn't define", () => {
    expect(roundStateSchema.safeParse({ ...round("ready"), status: "paused" }).success).toBe(false);
  });
});
