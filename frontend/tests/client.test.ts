import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api/client";

import { doneRunReport, metricCatalogue } from "./fixtures/reports";
import { lineage, step1Comparison } from "./fixtures/improvement";
import { proposalReview } from "./fixtures/proposal-review";

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("reads paged proposals and one proposal through the shared review response schema", async () => {
    const fetchMock = mockFetch(200, proposalReview);
    const result = await api.listProposals("reviewed", { level: "speciality", offset: 0, limit: 50 });
    expect(result.proposals[0].decision?.existing_concept_id).toBe(20);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/pipeline/proposals?merge_run=reviewed&level=speciality&offset=0&limit=50", expect.anything(),
    );
    const detailFetch = mockFetch(200, { ...proposalReview, limit: 1 });
    await api.getProposal("reviewed", "proposal-1");
    expect(detailFetch).toHaveBeenLastCalledWith(
      "/api/pipeline/proposals/proposal-1?merge_run=reviewed", expect.anything(),
    );
  });

  it("requests through the Next proxy prefix and parses the response", async () => {
    const fetchMock = mockFetch(200, metricCatalogue);
    await expect(api.listMetrics()).resolves.toEqual(metricCatalogue);
    expect(fetchMock).toHaveBeenCalledWith("/api/pipeline/metrics", expect.anything());
  });

  it("posts the pipeline config as JSON when creating a run", async () => {
    const fetchMock = mockFetch(202, { run_id: "abc", status: "queued" });
    const config = { datasets: [], metrics: [] };
    await api.createRun(config);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST", body: JSON.stringify(config) });
  });

  it("surfaces FastAPI's error detail on a failed request", async () => {
    mockFetch(422, { detail: { message: "API dataset paths must be absolute server-side paths." } });
    const error = await api.createRun({ datasets: [], metrics: [] }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, detail: { message: expect.stringContaining("absolute") } });
    expect((error as ApiError).message).toContain("absolute");
  });

  it("uses FastAPI's string detail as the error message", async () => {
    mockFetch(404, { detail: "run not found" });
    await expect(api.getRun("missing")).rejects.toThrow("run not found");
  });

  it("joins request-validation messages into the error message", async () => {
    mockFetch(422, { detail: [{ loc: ["body", "datasets"], msg: "Field required" }] });
    await expect(api.createRun({ datasets: [], metrics: [] })).rejects.toThrow("Field required");
  });

  it("falls back to the status code when the detail is unreadable", async () => {
    mockFetch(500, null);
    await expect(api.listMetrics()).rejects.toThrow("status 500");
  });

  it("fails loudly when the response shape drifts from the schema", async () => {
    mockFetch(200, { ...doneRunReport, status: "cancelled" });
    await expect(api.getRun(doneRunReport.run_id)).rejects.toThrow();
  });

  it("builds a taxonomy search request from the source and options given", async () => {
    const fetchMock = mockFetch(200, []);
    await api.searchTaxonomy({ runId: "abc", dataset: "original" }, { q: "tax", level: "tags", limit: 10 });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/pipeline/taxonomy/search?run_id=abc&dataset=original&q=tax&level=tags&limit=10",
      expect.anything(),
    );
  });

  it("omits unset taxonomy search options rather than sending them empty", async () => {
    const fetchMock = mockFetch(200, []);
    await api.searchTaxonomy({ runId: "abc", dataset: "original" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/pipeline/taxonomy/search?run_id=abc&dataset=original",
      expect.anything(),
    );
  });

  it("asks for one page of a round's review queue, leaving out an empty search", async () => {
    const empty = { tags: [], proposals: [], total: 0, offset: 0, limit: 25, undecided: 0, decided: 0, proposals_undecided: 0 };
    const plain = mockFetch(200, empty);
    await api.getReview("r1", { state: "undecided", offset: 0, limit: 25 });
    expect(plain).toHaveBeenCalledWith("/api/pipeline/rounds/r1/review?state=undecided&offset=0&limit=25", expect.anything());

    const searched = mockFetch(200, empty);
    await api.getReview("r1", { state: "all", q: "Adobe", offset: 25, limit: 25 });
    expect(searched).toHaveBeenCalledWith("/api/pipeline/rounds/r1/review?state=all&offset=25&limit=25&q=Adobe", expect.anything());
  });

  it("encodes a temporary proposal id when fetching concept detail", async () => {
    const fetchMock = mockFetch(200, {
      id: "skillsfuture:501", name: "x", level: "categories", dataset: "skillsfuture", paths: [], children: [], tags: [],
    });
    await api.getTaxonomyConcept({ runId: "abc", dataset: "skillsfuture" }, "categories", "skillsfuture:501");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/pipeline/taxonomy/categories/skillsfuture%3A501?run_id=abc&dataset=skillsfuture",
      expect.anything(),
    );
  });
});

describe("api client: lineage and comparisons", () => {
  it("requests a comparison with subjects as query values", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(step1Comparison), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await api.compareRuns({ runId: "r1", subject: "stage0_original" }, { runId: "r1", subject: "b 2" });
    expect(result.comparable).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/pipeline/runs/r1/compare/r1?before_subject=stage0_original&after_subject=b+2", expect.anything(),
    );
  });

  it("requests a comparison without subjects when none are named", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(step1Comparison), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await api.compareRuns({ runId: "a" }, { runId: "b" });
    expect(fetchMock).toHaveBeenCalledWith("/api/pipeline/runs/a/compare/b", expect.anything());
  });

  it("parses GET /lineage", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(lineage), { status: 200 })));
    await expect(api.getLineage()).resolves.toMatchObject({ chains: [{ stages: expect.any(Array) }, expect.anything()] });
  });
});

describe("api client: light run reports", () => {
  it("asks for a run without details only when told to", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify(doneRunReport), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await api.getRun("abc", { details: false });
    await api.getRun("abc");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/pipeline/runs/abc?details=false", "/api/pipeline/runs/abc"]);
  });
});
