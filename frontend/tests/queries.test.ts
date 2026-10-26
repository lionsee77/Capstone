import { describe, expect, it } from "vitest";

import { hasOlderRuns, pollWhileActive, RUN_POLL_INTERVAL_MS, RUNS_PAGE_SIZE, runQuery } from "@/lib/api/queries";

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
