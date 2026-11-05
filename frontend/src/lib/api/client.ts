/**
 * Thin fetch wrapper for the taxonomy pipeline API.
 *
 * Requests go to `/api/pipeline/*`, an allowlisted route handler that
 * forwards to `TAXONOMY_API_URL` (see `proxy.ts`). Proxying through Next keeps
 * the browser same-origin, so the FastAPI app needs no CORS config. Every
 * response is validated with zod.
 */
import type { z } from "zod";

import {
  type ConceptLevel,
  createRunResponseSchema,
  metricCatalogueSchema,
  type PipelineConfig,
  proposalReviewReportSchema,
  runComparisonSchema,
  runReportSchema,
  runSummarySchema,
  taxonomyConceptDetailSchema,
  taxonomyConceptSummarySchema,
  taxonomyLineageSchema,
} from "./schemas";

export const API_BASE = "/api/pipeline";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(describeDetail(detail) ?? `API request failed with status ${status}`);
    this.name = "ApiError";
  }
}

/**
 * Pull a human-readable message out of FastAPI's `detail`: a plain string
 * (`HTTPException(detail="run not found")`), an object with a `message`
 * (our own structured errors), or a request-validation error list.
 */
function describeDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : null))
      .filter((msg): msg is string => msg !== null);
    return messages.length > 0 ? messages.join("; ") : null;
  }
  if (detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string") {
    return detail.message;
  }
  return null;
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

/** Identifies which loaded dataset to read: a past run, and a dataset name within its config. */
export type TaxonomySource = { runId: string; dataset: string };

/** One side of a comparison: a stored run, and which of its taxonomies (dataset name) when it scored several. */
export type RunSubject = { runId: string; subject?: string | null };

export const api = {
  // Fetch saved screening evidence through the same validated boundary as other dashboard data.
  listProposals: (mergeRun: string, options: { level?: "category" | "speciality"; offset?: number; limit?: number } = {}) => {
    const params = new URLSearchParams({ merge_run: mergeRun });
    if (options.level) params.set("level", options.level);
    if (options.offset !== undefined) params.set("offset", String(options.offset));
    if (options.limit !== undefined) params.set("limit", String(options.limit));
    return request(`/proposals?${params}`, proposalReviewReportSchema);
  },
  // Retrieve one proposal with its original suggestion and saved reviewer decision kept separate.
  getProposal: (mergeRun: string, proposalId: string) => request(
    `/proposals/${encodeURIComponent(proposalId)}?${new URLSearchParams({ merge_run: mergeRun })}`,
    proposalReviewReportSchema,
  ),
  listMetrics: () => request("/metrics", metricCatalogueSchema),
  listRuns: (limit = 50) => request(`/runs?limit=${limit}`, runSummarySchema.array()),
  /** A run's report; `details: false` leaves out each result's (possibly megabytes of) details. */
  getRun: (runId: string, options: { details?: boolean } = {}) =>
    request(`/runs/${encodeURIComponent(runId)}${options.details === false ? "?details=false" : ""}`, runReportSchema),
  /** Compare two stored runs; a run that scored several taxonomies needs its subject (dataset name). */
  compareRuns: (before: RunSubject, after: RunSubject) => {
    const params = new URLSearchParams();
    if (before.subject) params.set("before_subject", before.subject);
    if (after.subject) params.set("after_subject", after.subject);
    const query = params.size > 0 ? `?${params}` : "";
    return request(
      `/runs/${encodeURIComponent(before.runId)}/compare/${encodeURIComponent(after.runId)}${query}`,
      runComparisonSchema,
    );
  },
  getLineage: () => request("/lineage", taxonomyLineageSchema),
  createRun: (config: PipelineConfig) =>
    request("/runs", createRunResponseSchema, { method: "POST", body: JSON.stringify(config) }),
  searchTaxonomy: (source: TaxonomySource, options: { q?: string; level?: ConceptLevel; limit?: number } = {}) => {
    const params = new URLSearchParams({ run_id: source.runId, dataset: source.dataset });
    if (options.q) params.set("q", options.q);
    if (options.level) params.set("level", options.level);
    if (options.limit) params.set("limit", String(options.limit));
    return request(`/taxonomy/search?${params}`, taxonomyConceptSummarySchema.array());
  },
  getTaxonomyConcept: (source: TaxonomySource, level: ConceptLevel, conceptId: string) => {
    const params = new URLSearchParams({ run_id: source.runId, dataset: source.dataset });
    return request(
      `/taxonomy/${level}/${encodeURIComponent(conceptId)}?${params}`,
      taxonomyConceptDetailSchema,
    );
  },
};
