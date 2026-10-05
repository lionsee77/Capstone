import { describe, expect, it } from "vitest";

import { isRunFinished, pipelineConfigSchema, runReportSchema } from "@/lib/api/schemas";

import { doneRunReport } from "./fixtures/reports";

describe("runReportSchema", () => {
  it("accepts a finished report from the API", () => {
    expect(runReportSchema.parse(doneRunReport).results[0].metric_id).toBe("integrity");
  });

  it("rejects a status the backend does not emit", () => {
    expect(runReportSchema.safeParse({ ...doneRunReport, status: "cancelled" }).success).toBe(false);
  });
});

describe("pipelineConfigSchema", () => {
  const dataset = { name: "skillsfuture", type: "sql_dump", paths: ["/data/sf.sql"] };

  it("defaults table_map and metric params like the Pydantic model", () => {
    const config = pipelineConfigSchema.parse({ datasets: [dataset], metrics: [{ id: "integrity" }] });
    expect(config.datasets[0].table_map).toEqual({});
    expect(config.metrics[0].params).toEqual({});
  });

  it("rejects relative dataset paths, which the API refuses", () => {
    const result = pipelineConfigSchema.safeParse({
      datasets: [{ ...dataset, paths: ["../sf.sql"] }],
      metrics: [{ id: "integrity" }],
    });
    expect(result.success).toBe(false);
  });

  it("requires exactly one workbook path for SSIC and SSOC references", () => {
    const withPaths = (type: string, paths: string[]) =>
      pipelineConfigSchema.safeParse({ datasets: [{ name: type, type, paths }], metrics: [{ id: "breadth" }] });

    expect(withPaths("ssic", ["/data/ssic.xlsx"]).success).toBe(true);
    expect(withPaths("ssoc", ["/data/a.xlsx", "/data/b.xlsx"]).success).toBe(false);
    // SQL dumps legitimately span several files.
    expect(withPaths("sql_dump", ["/data/a.sql", "/data/b.sql"]).success).toBe(true);
  });

  it("requires at least one dataset and one metric", () => {
    expect(pipelineConfigSchema.safeParse({ datasets: [], metrics: [] }).success).toBe(false);
  });
});

describe("isRunFinished", () => {
  it("treats only done and failed as terminal", () => {
    expect(["queued", "running", "done", "failed"].map((s) => isRunFinished(s as never))).toEqual([
      false,
      false,
      true,
      true,
    ]);
  });
});
