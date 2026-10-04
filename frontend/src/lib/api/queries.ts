/**
 * TanStack Query hooks - the only way components should talk to the API.
 * Query keys live in `queryKeys` so invalidation stays in one place.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import { isRunFinished, type PipelineConfig } from "./schemas";

const RUN_POLL_INTERVAL_MS = 2_000;

export const queryKeys = {
  metrics: ["metrics"] as const,
  runs: ["runs"] as const,
  run: (runId: string) => ["runs", runId] as const,
};

export function useMetricCatalogue() {
  return useQuery({ queryKey: queryKeys.metrics, queryFn: api.listMetrics, staleTime: Infinity });
}

export function useRuns() {
  return useQuery({ queryKey: queryKeys.runs, queryFn: () => api.listRuns() });
}

/** Polls while the run is queued/running, stops once it is done or failed. */
export function useRun(runId: string) {
  return useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: () => api.getRun(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && isRunFinished(status) ? false : RUN_POLL_INTERVAL_MS;
    },
  });
}

export function useCreateRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (config: PipelineConfig) => api.createRun(config),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
  });
}
