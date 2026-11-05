import { describe, expect, it } from "vitest";

import { runReportSchema } from "@/lib/api/schemas";
import { runSubjects } from "@/lib/run-view";

import { lineageRunReport } from "./fixtures/improvement";

describe("runSubjects", () => {
  it("lists the taxonomies a run scored, not the sources it only read", () => {
    expect(runSubjects(runReportSchema.parse(lineageRunReport))).toEqual([
      "stage0_original", "stage1_skillsfuture", "stage2_onet",
    ]);
  });

  it("is empty for a run whose results record no params", () => {
    const report = runReportSchema.parse({ ...lineageRunReport, results: [{ metric_id: "x", status: "ok", summary: "", details: {} }] });
    expect(runSubjects(report)).toEqual([]);
  });
});
