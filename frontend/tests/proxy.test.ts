import { describe, expect, it } from "vitest";

import { checkWriteRequest, decideProxyRequest, resolveUpstream, upstreamHeaders } from "@/lib/api/proxy";

const env = { TAXONOMY_API_URL: "http://pipeline:8000/" };
const resolve = (method: string, path: string, query = "", overrides = {}) =>
  resolveUpstream(method, path.split("/").filter(Boolean), new URLSearchParams(query), { ...env, ...overrides });

describe("pipeline proxy allowlist", () => {
  it("forwards only bounded read requests for saved proposals", () => {
    expect(resolve("GET", "proposals", "merge_run=reviewed&level=speciality&offset=50&limit=50&extra=ignored"))
      .toEqual({ ok: true,
        url: "http://pipeline:8000/proposals?merge_run=reviewed&level=speciality&offset=50&limit=50" });
    expect(resolve("GET", "proposals/proposal-1", "merge_run=reviewed"))
      .toEqual({ ok: true, url: "http://pipeline:8000/proposals/proposal-1?merge_run=reviewed" });
    for (const query of ["", "merge_run=../private", "merge_run=C:\\private", "merge_run=reviewed&limit=201",
      "merge_run=reviewed&limit=0", "merge_run=reviewed&offset=-1", "merge_run=reviewed&level=tags"]) {
      expect(resolve("GET", "proposals", query)).toMatchObject({ ok: false, status: 404 });
    }
    expect(resolve("GET", "proposals/..", "merge_run=reviewed")).toMatchObject({ ok: false });
    expect(resolve("POST", "proposals/proposal-1", "merge_run=reviewed")).toMatchObject({ ok: false });
  });

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

  const runAndDataset = "run_id=3f2a9c0e&dataset=original";

  it("forwards a taxonomy search with the identity it requires", () => {
    expect(resolve("GET", "taxonomy/search", runAndDataset)).toEqual({
      ok: true,
      url: "http://pipeline:8000/taxonomy/search?run_id=3f2a9c0e&dataset=original",
    });
  });

  it("forwards optional search filters and drops a malformed limit", () => {
    expect(resolve("GET", "taxonomy/search", `${runAndDataset}&q=tax&level=tags&limit=10`)).toEqual({
      ok: true,
      url: "http://pipeline:8000/taxonomy/search?run_id=3f2a9c0e&dataset=original&q=tax&level=tags&limit=10",
    });
    expect(resolve("GET", "taxonomy/search", `${runAndDataset}&limit=not-a-number`)).toEqual({
      ok: true,
      url: "http://pipeline:8000/taxonomy/search?run_id=3f2a9c0e&dataset=original",
    });
  });

  it("forwards a dataset name with spaces and parentheses, not just word characters", () => {
    // Dataset names are an unrestricted `name: str` in the pipeline config -
    // "Original (NUS)" is a real, valid name, not just "original".
    expect(resolve("GET", "taxonomy/search", "run_id=3f2a9c0e&dataset=Original+%28NUS%29")).toEqual({
      ok: true,
      url: "http://pipeline:8000/taxonomy/search?run_id=3f2a9c0e&dataset=Original+%28NUS%29",
    });
  });

  it("rejects a taxonomy search missing its required run_id or dataset", () => {
    expect(resolve("GET", "taxonomy/search", "dataset=original")).toMatchObject({ ok: false, status: 404 });
    expect(resolve("GET", "taxonomy/search", "run_id=3f2a9c0e")).toMatchObject({ ok: false, status: 404 });
  });

  it("rejects a taxonomy search with an unknown level", () => {
    expect(resolve("GET", "taxonomy/search", `${runAndDataset}&level=not-a-level`)).toMatchObject({
      ok: false,
      status: 404,
    });
  });

  it("forwards a taxonomy concept lookup, including a temporary proposal id", () => {
    expect(resolve("GET", "taxonomy/categories/1", runAndDataset)).toEqual({
      ok: true,
      url: "http://pipeline:8000/taxonomy/categories/1?run_id=3f2a9c0e&dataset=original",
    });
    // Next.js decodes dynamic route segments before this function sees them,
    // so a colon in a temporary proposal id arrives literal, not pre-encoded.
    expect(resolve("GET", "taxonomy/categories/skillsfuture:501", runAndDataset)).toEqual({
      ok: true,
      url: "http://pipeline:8000/taxonomy/categories/skillsfuture%3A501?run_id=3f2a9c0e&dataset=original",
    });
  });

  it("rejects a taxonomy concept lookup with an unknown level", () => {
    expect(resolve("GET", "taxonomy/not-a-level/1", runAndDataset)).toMatchObject({ ok: false, status: 404 });
  });

  it("rejects a taxonomy concept lookup missing its required run_id or dataset", () => {
    expect(resolve("GET", "taxonomy/categories/1", "dataset=original")).toMatchObject({ ok: false, status: 404 });
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

describe("write request guard", () => {
  const sameOrigin = {
    "content-type": "application/json",
    "sec-fetch-site": "same-origin",
    origin: "http://localhost:3000",
    host: "localhost:3000",
  };
  const check = (overrides: Record<string, string | null> = {}) => {
    const values: Record<string, string | null> = { ...sameOrigin, ...overrides };
    return checkWriteRequest({ get: (name: string) => values[name.toLowerCase()] ?? null });
  };

  it("allows a same-origin JSON request from the dashboard", () => {
    expect(check()).toBeNull();
    expect(check({ "content-type": "application/json; charset=utf-8" })).toBeNull();
  });

  it("allows non-browser clients that send no fetch metadata or origin", () => {
    expect(check({ "sec-fetch-site": null, origin: null })).toBeNull();
  });

  it("rejects bodies a cross-site page can send without a preflight", () => {
    for (const contentType of ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", null]) {
      expect(check({ "content-type": contentType })).toMatchObject({ ok: false, status: 415 });
    }
  });

  it("rejects cross-site requests by fetch metadata", () => {
    for (const site of ["cross-site", "same-site", "none"]) {
      expect(check({ "sec-fetch-site": site })).toMatchObject({ ok: false, status: 403 });
    }
  });

  it("rejects a foreign or opaque Origin when fetch metadata is missing", () => {
    expect(check({ "sec-fetch-site": null, origin: "https://evil.example" })).toMatchObject({ status: 403 });
    expect(check({ "sec-fetch-site": null, origin: "null" })).toMatchObject({ status: 403 });
  });

  it("trusts Sec-Fetch-Site over Origin behind a proxy that rewrites Host", () => {
    // nginx without `proxy_set_header Host`: Next sees the upstream name, the browser the public one.
    expect(check({ origin: "https://dashboard.example.com", host: "frontend:3000" })).toBeNull();
  });

  it("compares Origin with X-Forwarded-Host when the browser sends no fetch metadata", () => {
    const behindProxy = { "sec-fetch-site": null, origin: "https://dashboard.example.com", host: "frontend:3000" };
    expect(check({ ...behindProxy, "x-forwarded-host": "dashboard.example.com" })).toBeNull();
    expect(check(behindProxy)).toMatchObject({ status: 403 });
  });
});

describe("proxy decision order", () => {
  const textPlain = { get: (name: string) => (name.toLowerCase() === "content-type" ? "text/plain" : null) };
  const decide = (method: string, path: string, env = {}) =>
    decideProxyRequest(method, path.split("/"), new URLSearchParams(), textPlain, { ...env });

  it("reports an unknown path as 404 before judging the body", () => {
    expect(decide("POST", "metrics", { PIPELINE_ALLOW_RUN_CREATION: "true" })).toMatchObject({ status: 404 });
  });

  it("reports disabled run creation as 403 before judging the body", () => {
    expect(decide("POST", "runs")).toMatchObject({ status: 403, reason: expect.stringContaining("disabled") });
  });

  it("applies the write guard once a POST would be forwarded", () => {
    expect(decide("POST", "runs", { PIPELINE_ALLOW_RUN_CREATION: "true" })).toMatchObject({ status: 415 });
  });

  it("does not apply the write guard to reads", () => {
    expect(decide("GET", "metrics")).toMatchObject({ ok: true });
  });
});

describe("upstreamHeaders", () => {
  it("adds the API token server-side when one is configured", () => {
    expect(upstreamHeaders(null, { TAXONOMY_API_TOKEN: "s3cret" })).toEqual({ Authorization: "Bearer s3cret" });
  });

  it("forwards a body's content type alongside the token", () => {
    expect(upstreamHeaders("application/json", { TAXONOMY_API_TOKEN: "s3cret" })).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer s3cret",
    });
  });

  it("sends no Authorization header without a token, so the API's own check decides", () => {
    expect(upstreamHeaders(null, {})).toEqual({});
    expect(upstreamHeaders(null, { TAXONOMY_API_TOKEN: "" })).toEqual({});
  });
});

describe("proxy routes for lineage and comparisons", () => {
  it("forwards GET /lineage and nothing below it", () => {
    expect(resolve("GET", "lineage")).toEqual({ ok: true, url: "http://pipeline:8000/lineage" });
    expect(resolve("GET", "lineage/x")).toMatchObject({ ok: false, status: 404 });
    expect(resolve("POST", "lineage")).toMatchObject({ ok: false, status: 404 });
  });

  it("forwards a comparison of two run ids with optional subjects", () => {
    expect(resolve("GET", "runs/aaa/compare/bbb")).toEqual({ ok: true, url: "http://pipeline:8000/runs/aaa/compare/bbb" });
    expect(resolve("GET", "runs/aaa/compare/aaa", "before_subject=stage 0 (orig)&after_subject=b&x=1")).toEqual({
      ok: true,
      url: "http://pipeline:8000/runs/aaa/compare/aaa?before_subject=stage+0+%28orig%29&after_subject=b",
    });
  });

  it("refuses malformed comparison paths and subjects", () => {
    for (const path of ["runs/aaa/compare", "runs/aaa/compare/bbb/extra", "runs/a.a/compare/bbb", "runs/aaa/other/bbb"]) {
      expect(resolve("GET", path)).toMatchObject({ ok: false, status: 404 });
    }
    expect(resolve("GET", "runs/aaa/compare/bbb", "before_subject=%01")).toMatchObject({ ok: false, status: 404 });
    expect(resolve("POST", "runs/aaa/compare/bbb")).toMatchObject({ ok: false });
  });
});

describe("proxy: light run reports", () => {
  it("forwards details=false for one run and drops any other query", () => {
    expect(resolve("GET", "runs/abc", "details=false")).toEqual({ ok: true, url: "http://pipeline:8000/runs/abc?details=false" });
    expect(resolve("GET", "runs/abc", "details=true&x=1")).toEqual({ ok: true, url: "http://pipeline:8000/runs/abc" });
  });
});
