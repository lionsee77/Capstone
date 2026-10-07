import { describe, expect, it } from "vitest";

import { hasOlderRuns, pollWhileActive, RUN_POLL_INTERVAL_MS, RUNS_PAGE_SIZE } from "@/lib/api/queries";

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
    expect(hasOlderRuns(RUNS_PAGE_SIZE, RUNS_PAGE_SIZE, false)).toBe(true);
    expect(hasOlderRuns(12, RUNS_PAGE_SIZE, false)).toBe(false);
  });

  it("keeps offering them while the next page loads behind the previous rows", () => {
    // Clicked "load older": limit is now 100, but the 50 rows shown came from the limit-50 request.
    expect(hasOlderRuns(RUNS_PAGE_SIZE, 2 * RUNS_PAGE_SIZE, true)).toBe(true);
  });

  it("stops once the larger page comes back short", () => {
    expect(hasOlderRuns(73, 2 * RUNS_PAGE_SIZE, false)).toBe(false);
  });
});
