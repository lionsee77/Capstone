import { describe, expect, it } from "vitest";

import { runComparisonSchema, runReportSchema, taxonomyLineageSchema } from "@/lib/api/schemas";
import {
  chainOptions,
  comparisonPairs,
  highlights,
  overallChange,
  scoredStages,
  stageCounts,
  stageLabel,
  trendGroups,
} from "@/lib/improvement";

import {
  lineage as lineagePayload,
  lineageRunReport,
  overallComparison,
  step1Comparison,
  step2Comparison,
} from "./fixtures/improvement";

const lineage = taxonomyLineageSchema.parse(lineagePayload);
const report = runReportSchema.parse(lineageRunReport);
const steps = [runComparisonSchema.parse(step1Comparison), runComparisonSchema.parse(step2Comparison)];
const overall = runComparisonSchema.parse(overallComparison);
const [chain] = chainOptions(lineage);
const scored = scoredStages(chain, chain.runs[0]);

describe("chain options", () => {
  it("resolves the longest chain's stages and its best run from ids", () => {
    expect(chain.stages.map((stage) => stage.id)).toEqual(["baseline:original", "judged-1015", "onet-1031"]);
    expect(chain.runs[0].judged).toBe(true);
    expect(chain.stages[1].sources).toEqual(["skillsfuture"]);
  });

  it("labels stages Baseline 1, 2, 3 by position", () => {
    expect(chain.stages.map((_, position) => stageLabel(position))).toEqual(["Baseline 1", "Baseline 2", "Baseline 3"]);
  });

  it("lists each stage the run scored with its dataset name", () => {
    expect(scored.map((item) => [item.position, item.dataset])).toEqual([
      [0, "stage0_original"], [1, "stage1_skillsfuture"], [2, "stage2_onet"],
    ]);
  });

  it("skips stages a run did not score", () => {
    const partial = { ...chain.runs[0], datasets: { "onet-1031": "enriched" } };
    expect(scoredStages(chain, partial).map((item) => item.position)).toEqual([2]);
  });
});

describe("comparison pairs", () => {
  it("compares each stage with the next, and the first with the last", () => {
    const pairs = comparisonPairs("run", scored);
    expect(pairs.steps.map(([a, b]) => [a.subject, b.subject])).toEqual([
      ["stage0_original", "stage1_skillsfuture"], ["stage1_skillsfuture", "stage2_onet"],
    ]);
    expect(pairs.overall?.map((side) => side.subject)).toEqual(["stage0_original", "stage2_onet"]);
  });

  it("needs no separate first-to-last comparison for two stages", () => {
    expect(comparisonPairs("run", scored.slice(0, 2)).overall).toBeNull();
  });
});

describe("trend groups", () => {
  const groups = trendGroups(report, scored, steps, overall);
  const find = (metricId: string, qualifier: string | null = null) =>
    groups.find((group) => group.metricId === metricId && group.qualifier === qualifier);

  it("makes one group per scoring entry, naming saturation entries by their incoming source", () => {
    expect(groups.map((group) => [group.metricId, group.qualifier])).toEqual([
      ["integrity", null], ["depth", null], ["breadth", null],
      ["saturation", "skillsfuture"], ["saturation", "onet"],
    ]);
  });

  it("puts the API's value for every stage in one row", () => {
    const coverage = find("breadth")?.rows.find((row) => row.key === "occupational.coverage");
    expect(coverage?.values).toEqual([0.11, 0.29, 0.297]);
    expect(coverage?.steps.map((step) => step?.verdict)).toEqual(["improved", "improved"]);
    expect(coverage?.overall?.change).toBeCloseTo(0.187);
  });

  it("keeps the API's verdict for a measure that got worse", () => {
    const duplicates = find("integrity")?.rows.find((row) => row.key === "exact_duplicate_rows.specialities");
    expect(duplicates?.values).toEqual([51, 182, 188]);
    expect(overallChange(duplicates!)?.verdict).toBe("deteriorated");
  });

  it("shows saturation against each source falling as rounds absorb it", () => {
    const onet = find("saturation", "onet")?.rows.find((row) => row.key === "new_rate_min");
    expect(onet?.values).toEqual([0.438, 0.283, 0.162]);
    expect(onet?.unit).toBe("rate");
  });

  it("collects each entry's comparability issues once", () => {
    expect(groups.every((group) => group.issues.length === 0)).toBe(true);
  });
});

describe("highlights", () => {
  const groups = trendGroups(report, scored, steps, overall);

  it("picks moved rates with a better direction, improvements first and largest first", () => {
    const picked = highlights(groups);
    expect(picked.length).toBeGreaterThan(0);
    expect(picked.every(({ row }) => row.unit === "rate" && row.better !== null)).toBe(true);
    const verdicts = picked.map(({ row }) => overallChange(row)?.verdict);
    const firstWorse = verdicts.indexOf("deteriorated");
    expect(firstWorse === -1 || verdicts.slice(firstWorse).every((verdict) => verdict !== "improved")).toBe(true);
    const improved = picked.filter(({ row }) => overallChange(row)?.verdict === "improved");
    const sizes = improved.map(({ row }) => Math.abs(overallChange(row)?.change ?? 0));
    expect(sizes).toEqual([...sizes].sort((a, b) => b - a));
    expect([picked[0].group.qualifier, picked[0].row.key]).toEqual(["skillsfuture", "new_rate_min"]);
  });

  it("respects the limit", () => {
    expect(highlights(groups, 2)).toHaveLength(2);
  });
});

describe("stage counts", () => {
  it("reads each stage's size from the run's own dataset counts", () => {
    expect(stageCounts(report, scored).map((counts) => counts.tags)).toEqual([676, 1924, 2068]);
  });
});
