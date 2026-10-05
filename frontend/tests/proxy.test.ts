import { describe, expect, it } from "vitest";

import { resolveUpstream } from "@/lib/api/proxy";

const env = { TAXONOMY_API_URL: "http://pipeline:8000/" };
const resolve = (method: string, path: string, query = "", overrides = {}) =>
  resolveUpstream(method, path.split("/").filter(Boolean), new URLSearchParams(query), { ...env, ...overrides });

describe("pipeline proxy allowlist", () => {
  it("forwards the read endpoints the dashboard uses", () => {
    expect(resolve("GET", "metrics")).toEqual({ ok: true, url: "http://pipeline:8000/metrics" });
    expect(resolve("GET", "runs", "limit=100")).toEqual({ ok: true, url: "http://pipeline:8000/runs?limit=100" });
    expect(resolve("GET", "runs/3f2a9c0e")).toEqual({ ok: true, url: "http://pipeline:8000/runs/3f2a9c0e" });
  });

  it("drops a malformed limit instead of forwarding it", () => {
    expect(resolve("GET", "runs", "limit=50&x=1;drop")).toEqual({ ok: true, url: "http://pipeline:8000/runs?limit=50" });
    expect(resolve("GET", "runs", "limit=-1")).toEqual({ ok: true, url: "http://pipeline:8000/runs" });
  });

  it("does not expose endpoints the dashboard does not use", () => {
    for (const path of ["docs", "openapi.json", "runs/abc/extra", "runs/..%2Fmetrics"]) {
      expect(resolve("GET", path)).toMatchObject({ ok: false, status: 404 });
    }
  });

  it("refuses run creation unless it is explicitly enabled", () => {
    expect(resolve("POST", "runs")).toMatchObject({ ok: false, status: 403 });
    expect(resolve("POST", "runs", "", { PIPELINE_ALLOW_RUN_CREATION: "1" })).toMatchObject({ ok: false });
    expect(resolve("POST", "runs", "", { PIPELINE_ALLOW_RUN_CREATION: "true" })).toEqual({
      ok: true,
      url: "http://pipeline:8000/runs",
    });
  });

  it("only allows POST on the run collection", () => {
    expect(resolve("POST", "metrics", "", { PIPELINE_ALLOW_RUN_CREATION: "true" })).toMatchObject({ ok: false });
  });
});
