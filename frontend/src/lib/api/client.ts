/**
 * Thin fetch wrapper for the taxonomy pipeline API.
 *
 * Requests go to `/api/pipeline/*`, which `next.config.ts` rewrites to
 * `TAXONOMY_API_URL`. Proxying through Next keeps the browser same-origin, so
 * the FastAPI app needs no CORS config. Every response is validated with zod.
 */
import type { z } from "zod";

import {
  createRunResponseSchema,
  metricCatalogueSchema,
  type PipelineConfig,
  runReportSchema,
  runSummarySchema,
} from "./schemas";

export const API_BASE = "/api/pipeline";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    // FastAPI puts error context under `detail`.
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
    throw new ApiError(response.status, detail);
  }
  return schema.parse(body);
}

export const api = {
  listMetrics: () => request("/metrics", metricCatalogueSchema),
  listRuns: (limit = 50) => request(`/runs?limit=${limit}`, runSummarySchema.array()),
  getRun: (runId: string) => request(`/runs/${encodeURIComponent(runId)}`, runReportSchema),
  createRun: (config: PipelineConfig) =>
    request("/runs", createRunResponseSchema, { method: "POST", body: JSON.stringify(config) }),
};
