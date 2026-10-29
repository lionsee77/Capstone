import { describe, expect, it } from "vitest";

import type { MetricResult } from "@/lib/api/schemas";
import { metricLabels } from "@/lib/metric-labels";

function result(metric_id: string, index?: number, params?: Record<string, unknown>): MetricResult {
  return { metric_id, status: "ok", summary: "", details: {}, index, params };
}

describe("metricLabels", () => {
  it("keeps a metric that runs once as its bare id", () => {
    expect(metricLabels([result("integrity", 0, { dataset: "a" }), result("depth", 1)])).toEqual([
      "integrity",
      "depth",
    ]);
  });

  it("labels repeated metrics by the params that differ", () => {
    const results = [
      result("integrity", 0, { dataset: "original", fuzzy_threshold: 85 }),
      result("integrity", 1, { dataset: "skillsfuture", fuzzy_threshold: 85 }),
    ];
    expect(metricLabels(results)).toEqual(["integrity (dataset=original)", "integrity (dataset=skillsfuture)"]);
  });

  it("joins list params", () => {
    const results = [result("saturation", 0, { incoming: ["a", "b"] }), result("saturation", 1, { incoming: ["c"] })];
    expect(metricLabels(results)).toEqual(["saturation (incoming=a,b)", "saturation (incoming=c)"]);
  });

  it("adds the position when repeats share their differing params", () => {
    const results = [
      result("integrity", 0, { dataset: "a" }),
      result("integrity", 1, { dataset: "a" }),
      result("integrity", 2, { dataset: "b" }),
    ];
    expect(metricLabels(results)).toEqual([
      "integrity (dataset=a) #0",
      "integrity (dataset=a) #1",
      "integrity (dataset=b)",
    ]);
  });

  it("keeps objects, booleans and nulls readable, in the pipeline's format", () => {
    const results = [
      result("depth", 0, { by_level: { tags: 0.8, specialities: 0.75 }, cross_level: true, model: null }),
      result("depth", 1, { by_level: { tags: 0.9 }, cross_level: false, model: "m" }),
    ];
    expect(metricLabels(results)).toEqual([
      "depth (by_level={specialities:0.75,tags:0.8}, cross_level=true, model=null)",
      "depth (by_level={tags:0.9}, cross_level=false, model=m)",
    ]);
  });

  it("falls back to the config position for identical or unrecorded params", () => {
    expect(metricLabels([result("depth", 0, { dataset: "a" }), result("depth", 3, { dataset: "a" })])).toEqual([
      "depth #0",
      "depth #3",
    ]);
    expect(metricLabels([result("depth"), result("depth")])).toEqual(["depth #0", "depth #1"]);
  });
});
