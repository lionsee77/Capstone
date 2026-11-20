import { describe, expect, it } from "vitest";

import { formatChange, formatValue, verdictMeta } from "@/lib/format";

describe("formatValue", () => {
  it("shows a rate as a percentage with at most one decimal", () => {
    expect(formatValue(0.268, "rate")).toBe("26.8%");
    expect(formatValue(0.29, "rate")).toBe("29%");
    expect(formatValue(0, "rate")).toBe("0%");
  });

  it("shows a number with thousands separators and at most two decimals", () => {
    expect(formatValue(1248, "number")).toBe("1,248");
    expect(formatValue(10.384, "number")).toBe("10.38");
  });

  it("shows a missing value as a dash", () => {
    expect(formatValue(null, "rate")).toBe("–");
    expect(formatValue(undefined, "number")).toBe("–");
  });
});

describe("formatChange", () => {
  it("shows a rate's change in signed percentage points", () => {
    expect(formatChange(0.18, "rate")).toBe("+18 pts");
    expect(formatChange(-0.1533, "rate")).toBe("−15.3 pts");
    expect(formatChange(0, "rate")).toBe("0 pts");
    expect(formatChange(-0.0001, "rate")).toBe("−<0.1 pts");
  });

  it("shows a number's change signed", () => {
    expect(formatChange(185, "number")).toBe("+185");
    expect(formatChange(-2, "number")).toBe("−2");
    expect(formatChange(null, "number")).toBe("–");
  });
});

describe("verdictMeta", () => {
  it("gives improvements and deteriorations the status tones and the rest neutral ones", () => {
    expect(verdictMeta("improved")).toEqual({ label: "Improved", tone: "good" });
    expect(verdictMeta("deteriorated")).toEqual({ label: "Worse", tone: "bad" });
    expect(verdictMeta("changed").tone).toBe("neutral");
    expect(verdictMeta("not_compared").tone).toBe("muted");
  });
});
