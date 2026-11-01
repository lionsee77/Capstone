import { describe, expect, it } from "vitest";
import { changeEventSchema, changeHistorySchema } from "@/lib/api/schemas";

const event = {
  event_id: "e1", operation_id: "op1", dataset: "original", snapshot_id: "snapshot",
  concept_level: "category", concept_id: "42", concept_ref: "category:42", concept_name: "Computing",
  action: "add", before: null, after: { id: 42, name: "Computing" }, reason: "Approved",
  actor: "reviewer", occurred_at: "2026-10-30T00:00:00Z", proposal_ids: ["p1"],
  proposal_namespace: "pipeline", decision_ids: ["d1"], decision: null, scope: "snapshot",
  linked_reviews: [],
};

describe("audit schemas", () => {
  it("preserves original review evidence separately from the integration actor", () => {
    const review = { decision_id: "d1", decision: { action: "create", reason: "Confirmed" },
      review_event_ids: ["review1"], actor: "Original reviewer", occurred_at: "2026-10-29T00:00:00Z" };
    const parsed = changeEventSchema.parse({ ...event, actor: "Integrator", linked_reviews: [review] });
    expect(parsed.linked_reviews).toEqual([review]);
    expect(parsed.actor).toBe("Integrator");
    expect(changeEventSchema.parse({ ...event, linked_reviews: undefined }).linked_reviews).toEqual([]);
  });

  it("represents missing legacy attribution explicitly and rejects timestamps without a timezone", () => {
    const review = { decision_id: "d1", decision: { action: "create" } };
    const parsed = changeEventSchema.parse({ ...event, linked_reviews: [review] });
    expect(parsed.linked_reviews[0].actor).toBeNull();
    expect(parsed.linked_reviews[0].occurred_at).toBeNull();
    expect(changeEventSchema.safeParse({ ...event, linked_reviews: [
      { ...review, occurred_at: "2026-10-30T00:00:00" },
    ] }).success).toBe(false);
  });
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
