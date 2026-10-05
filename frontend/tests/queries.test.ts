import { describe, expect, it } from "vitest";

import { pollWhileActive, RUN_POLL_INTERVAL_MS } from "@/lib/api/queries";

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
