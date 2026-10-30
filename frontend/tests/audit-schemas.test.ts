import { describe, expect, it } from "vitest";
import { changeEventSchema, changeHistorySchema } from "@/lib/api/schemas";

const event = {
  event_id: "e1", operation_id: "op1", dataset: "original", snapshot_id: "snapshot",
  concept_level: "category", concept_id: "42", concept_ref: "category:42", concept_name: "Computing",
  action: "add", before: null, after: { id: 42, name: "Computing" }, reason: "Approved",
  actor: "reviewer", occurred_at: "2026-10-30T00:00:00Z", proposal_ids: ["p1"],
  proposal_namespace: "pipeline", decision_ids: ["d1"], decision: null, scope: "snapshot",
};

describe("audit schemas", () => {
  it("retains snapshot identity and proposal lineage in paginated history", () => {
    const history = changeHistorySchema.parse({ total: 1, offset: 0, limit: 50, changes: [event] });
    expect(history.changes[0]).toEqual(event);
  });

  it("accepts provisional concepts without inventing numeric IDs", () => {
    const review = { ...event, concept_id: null, concept_ref: "proposal:p1", action: "review_decision",
      scope: "review", decision: { action: "defer", reason: "Needs review" }, after: null };
    expect(changeEventSchema.parse(review).concept_id).toBeNull();
  });

  it("rejects misleading review scopes and missing decisions", () => {
    expect(changeEventSchema.safeParse({ ...event, scope: "review" }).success).toBe(false);
    expect(changeEventSchema.safeParse({ ...event, action: "review_decision", scope: "review" }).success).toBe(false);
    expect(changeEventSchema.safeParse({ ...event, occurred_at: "2026-10-30T00:00:00" }).success).toBe(false);
  });
});
