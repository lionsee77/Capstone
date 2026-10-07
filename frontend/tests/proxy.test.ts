import { describe, expect, it } from "vitest";

import { checkWriteRequest, decideProxyRequest, resolveUpstream } from "@/lib/api/proxy";

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
