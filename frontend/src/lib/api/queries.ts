/**
 * TanStack Query hooks - the only way components should talk to the API.
 * Query keys live in `queryKeys` so invalidation stays in one place.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import { isRunFinished, type PipelineConfig, type RunStatus } from "./schemas";

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

export const queryKeys = {
  metrics: ["metrics"] as const,
  runs: ["runs"] as const,
  runList: (limit: number) => ["runs", "list", limit] as const,
  run: (runId: string) => ["runs", "detail", runId] as const,
};

export function useMetricCatalogue() {
  return useQuery({ queryKey: queryKeys.metrics, queryFn: api.listMetrics, staleTime: Infinity });
}

/**
 * Newest `limit` runs. Polls while any listed run is still queued/running so
 * status badges update without a manual refresh. `GET /runs` has no offset,
 * so "load more" asks for a larger `limit`.
 */
export function useRuns(limit = RUNS_PAGE_SIZE) {
  return useQuery({
    queryKey: queryKeys.runList(limit),
    queryFn: () => api.listRuns(limit),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => pollWhileActive(query.state.data),
  });
}

/** Polls while the run is queued/running, stops once it is done or failed. */
export function useRun(runId: string) {
  return useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: () => api.getRun(runId),
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
