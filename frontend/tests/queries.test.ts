import { describe, expect, it } from "vitest";

import {
  apiHealthQuery,
  HEALTH_POLL_INTERVAL_MS,
  hasOlderRuns,
  pollWhileActive,
  RUN_POLL_INTERVAL_MS,
  RUNS_PAGE_SIZE,
  runQuery,
  runSummaryQuery,
} from "@/lib/api/queries";

describe("pollWhileActive", () => {
  it("keeps polling while any run is queued or running", () => {
    expect(pollWhileActive([{ status: "done" }, { status: "running" }])).toBe(RUN_POLL_INTERVAL_MS);
    expect(pollWhileActive([{ status: "queued" }])).toBe(RUN_POLL_INTERVAL_MS);
  });

  it("stops once every run is done or failed", () => {
    expect(pollWhileActive([{ status: "done" }, { status: "failed" }])).toBe(false);
    expect(pollWhileActive([])).toBe(false);
  });

  it("does not poll without data, so an unknown run id is not retried forever", () => {
    expect(pollWhileActive(undefined)).toBe(false);
  });
});

describe("hasOlderRuns", () => {
  it("offers older runs only after a full page", () => {
    expect(hasOlderRuns(RUNS_PAGE_SIZE, RUNS_PAGE_SIZE)).toBe(true);
    expect(hasOlderRuns(12, RUNS_PAGE_SIZE)).toBe(false);
  });

  it("stops once a larger page comes back short", () => {
    expect(hasOlderRuns(73, 2 * RUNS_PAGE_SIZE)).toBe(false);
  });
});

describe("runQuery", () => {
  it("stays disabled until a run is picked, so no request goes to /runs/", () => {
    expect(runQuery("").enabled).toBe(false);
  });

  it("fetches once a run id is set", () => {
    expect(runQuery("abc123").enabled).toBe(true);
    expect(runQuery("abc123").queryKey).toEqual(["runs", "detail", "abc123"]);
  });
});

describe("runSummaryQuery", () => {
  it("is disabled until a run is picked, and cached apart from the full report", () => {
    expect(runSummaryQuery("").enabled).toBe(false);
    expect(runSummaryQuery("abc123").queryKey).toEqual(["runs", "detail", "abc123", "summary"]);
    expect(runSummaryQuery("abc123").queryKey).not.toEqual(runQuery("abc123").queryKey);
  });
});

describe("apiHealthQuery", () => {
  it("re-checks the API on an interval, unlike the forever-cached metric catalogue", () => {
    expect(apiHealthQuery().refetchInterval).toBe(HEALTH_POLL_INTERVAL_MS);
    expect(apiHealthQuery().queryKey).toEqual(["health"]);
  });

  it("does not retry, so a stopped API shows as unreachable within one interval", () => {
    expect(apiHealthQuery().retry).toBe(false);
  });
});
