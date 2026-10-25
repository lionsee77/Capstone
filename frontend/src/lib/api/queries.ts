/**
 * TanStack Query hooks - the only way components should talk to the API.
 * Query keys live in `queryKeys` so invalidation stays in one place.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type TaxonomySource } from "./client";
import { type ConceptLevel, isRunFinished, type PipelineConfig, type RunStatus } from "./schemas";

export const RUN_POLL_INTERVAL_MS = 2_000;

/**
 * Poll interval for a set of runs: keep polling until every run has finished.
 * No data (not loaded yet, or the request failed, e.g. an unknown run id)
 * means no polling - the initial fetch runs regardless.
 */
export function pollWhileActive(runs: { status: RunStatus }[] | undefined): number | false {
  if (!runs) return false;
  return runs.some((run) => !isRunFinished(run.status)) ? RUN_POLL_INTERVAL_MS : false;
}

export const RUNS_PAGE_SIZE = 50;

/** A full page means older runs may exist; the API returns fewer once history runs out. */
export function hasOlderRuns(rowCount: number, limit: number): boolean {
  return rowCount >= limit;
}

export const queryKeys = {
  metrics: ["metrics"] as const,
  runs: ["runs"] as const,
  runList: (limit: number) => ["runs", "list", limit] as const,
  run: (runId: string) => ["runs", "detail", runId] as const,
  taxonomySearch: (source: TaxonomySource | null, q: string, level: ConceptLevel | "") =>
    ["taxonomy", "search", source?.runId, source?.dataset, q, level] as const,
  taxonomyConcept: (source: TaxonomySource | null, level: ConceptLevel, conceptId: string) =>
    ["taxonomy", "concept", source?.runId, source?.dataset, level, conceptId] as const,
};

export function useMetricCatalogue() {
  return useQuery({ queryKey: queryKeys.metrics, queryFn: api.listMetrics, staleTime: Infinity });
}

/**
 * Newest `limit` runs. Polls while any listed run is still queued/running so
 * status badges update without a manual refresh. `GET /runs` has no offset,
 * so loading older runs asks for a larger `limit` (see `useLoadOlderRuns`).
 */
export function useRuns(limit = RUNS_PAGE_SIZE) {
  return useQuery({
    ...runListQuery(limit),
    refetchInterval: (query) => pollWhileActive(query.state.data),
  });
}

/**
 * Fetches a larger page of runs *before* the table switches to it, so the rows
 * on screen stay put while it loads and if it fails (the button then works as
 * a retry). `onLoaded` gets the new limit once its data is in the cache.
 */
export function useLoadOlderRuns(onLoaded: (limit: number) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (limit: number) => queryClient.fetchQuery(runListQuery(limit)),
    onSuccess: (_runs, limit) => onLoaded(limit),
  });
}

function runListQuery(limit: number) {
  return { queryKey: queryKeys.runList(limit), queryFn: () => api.listRuns(limit) };
}

export function runQuery(runId: string) {
  return { queryKey: queryKeys.run(runId), queryFn: () => api.getRun(runId), enabled: runId !== "" };
}

/** Polls while the run is queued/running, stops once it is done or failed.
 * An empty `runId` (nothing picked yet) disables the query instead of
 * requesting `/runs/`. */
export function useRun(runId: string) {
  return useQuery({
    ...runQuery(runId),
    refetchInterval: (query) => pollWhileActive(query.state.data && [query.state.data]),
  });
}

export function useCreateRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (config: PipelineConfig) => api.createRun(config),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
  });
}

/** Name search over `source`'s dataset; disabled until a run and dataset are both chosen. */
export function useTaxonomySearch(source: TaxonomySource | null, q: string, level: ConceptLevel | "") {
  return useQuery({
    queryKey: queryKeys.taxonomySearch(source, q, level),
    queryFn: () => api.searchTaxonomy(source as TaxonomySource, { q: q || undefined, level: level || undefined }),
    enabled: source !== null,
  });
}

/** One concept's detail; disabled until `source` (the run + dataset it was found in) is known. */
export function useTaxonomyConcept(source: TaxonomySource | null, level: ConceptLevel, conceptId: string) {
  return useQuery({
    queryKey: queryKeys.taxonomyConcept(source, level, conceptId),
    queryFn: () => api.getTaxonomyConcept(source as TaxonomySource, level, conceptId),
    enabled: source !== null,
  });
}
