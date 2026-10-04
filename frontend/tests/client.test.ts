import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api/client";

import { doneRunReport, metricCatalogue } from "./fixtures/reports";

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
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
  });

  it("fails loudly when the response shape drifts from the schema", async () => {
    mockFetch(200, { ...doneRunReport, status: "cancelled" });
    await expect(api.getRun(doneRunReport.run_id)).rejects.toThrow();
  });
});
