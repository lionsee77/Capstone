/**
 * TanStack Query hooks - the only way components should talk to the API.
 * Query keys live in `queryKeys` so invalidation stays in one place.
 */
import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { isLookupTerm } from "@/lib/lookup";
import { isRoundActive } from "@/lib/rounds";

import { api, type DecisionSubmission, type ReviewQuery, type RunSubject, type TaxonomySource } from "./client";
import {
  type ConceptLevel,
  isRunFinished,
  type PipelineConfig,
  type RoundSettings,
  type RoundState,
  type RunStatus,
} from "./schemas";

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

/** Poll a round while it runs, so its stages update on their own and polling stops when it does. */
export function pollRound(round: RoundState | undefined): number | false {
  return isRoundActive(round) ? RUN_POLL_INTERVAL_MS : false;
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
  runSummary: (runId: string) => ["runs", "detail", runId, "summary"] as const,
  runComparison: (before: RunSubject, after: RunSubject) =>
    ["runs", "compare", before.runId, before.subject ?? null, after.runId, after.subject ?? null] as const,
  lineage: ["lineage"] as const,
  rounds: ["rounds"] as const,
  roundOptions: ["rounds", "options"] as const,
  round: (roundId: string) => ["rounds", "detail", roundId] as const,
  roundConfig: (roundId: string) => ["rounds", "detail", roundId, "config"] as const,
  review: (roundId: string) => ["rounds", "detail", roundId, "review"] as const,
  roundFiles: (roundId: string) => ["rounds", "detail", roundId, "files"] as const,
  roundFile: (roundId: string, name: string | null, sheet: string | null) =>
    ["rounds", "detail", roundId, "files", name, sheet] as const,
  health: ["health"] as const,
  taxonomySearch: (source: TaxonomySource | null, q: string, level: ConceptLevel | "") =>
    ["taxonomy", "search", source?.runId, source?.dataset, q, level] as const,
  tagLookup: (term: string, level: ConceptLevel | "", semantic: boolean) =>
    ["lookup", term, level, semantic] as const,
  taxonomyConcept: (source: TaxonomySource | null, level: ConceptLevel, conceptId: string) =>
    ["taxonomy", "concept", source?.runId, source?.dataset, level, conceptId] as const,
};

export function useMetricCatalogue() {
  return useQuery({ queryKey: queryKeys.metrics, queryFn: api.listMetrics, staleTime: Infinity });
}

export const HEALTH_POLL_INTERVAL_MS = 30_000;

/**
 * Whether the pipeline API answers now: `GET /metrics` re-checked every
 * `HEALTH_POLL_INTERVAL_MS` and on window focus, without retries, so a stopped
 * API shows within one interval. Separate from `useMetricCatalogue`, whose
 * result is cached forever and so says nothing about the API after its first load.
 */
export function apiHealthQuery() {
  return {
    queryKey: queryKeys.health,
    queryFn: api.listMetrics,
    refetchInterval: HEALTH_POLL_INTERVAL_MS,
    refetchOnWindowFocus: true,
    retry: false,
  } as const;
}

export function useApiHealth() {
  return useQuery(apiHealthQuery());
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

/** The run's report without result details (summaries, params, dataset counts); disabled for an empty id. */
export function runSummaryQuery(runId: string) {
  return {
    queryKey: queryKeys.runSummary(runId),
    queryFn: () => api.getRun(runId, { details: false }),
    enabled: runId !== "",
  };
}

/**
 * `useRun` without result details, for pages that summarise a run rather than draw its details:
 * a judged saturation run stores every concept it reviewed, tens of megabytes. Polls the same way.
 */
export function useRunSummary(runId: string | null) {
  return useQuery({
    ...runSummaryQuery(runId ?? ""),
    refetchInterval: (query) => pollWhileActive(query.state.data && [query.state.data]),
  });
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

/** Does a term exist in the newest snapshot? Disabled until the term is one the API would accept. */
export function useTagLookup(term: string, level: ConceptLevel | "", semantic: boolean) {
  return useQuery({
    queryKey: queryKeys.tagLookup(term, level, semantic),
    queryFn: () => api.lookupTerm(term, { level: level || undefined, semantic }),
    enabled: isLookupTerm(term),
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

export function useRoundOptions() {
  return useQuery({ queryKey: queryKeys.roundOptions, queryFn: api.getRoundOptions });
}

export function useRounds() {
  return useQuery({
    queryKey: queryKeys.rounds,
    queryFn: api.listRounds,
    refetchInterval: (query) => (query.state.data?.some(isRoundActive) ? RUN_POLL_INTERVAL_MS : false),
  });
}

export function useRound(roundId: string) {
  return useQuery({
    queryKey: queryKeys.round(roundId),
    queryFn: () => api.getRound(roundId),
    refetchInterval: (query) => pollRound(query.state.data),
  });
}

export function useRoundConfigText(roundId: string, enabled: boolean) {
  return useQuery({ queryKey: queryKeys.roundConfig(roundId), queryFn: () => api.getRoundConfigText(roundId), enabled });
}

export function useCreateRound() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { round_id: string; baseline_snapshot: string }) => api.createRound(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.rounds, exact: true }),
  });
}

export function useRoundFiles(roundId: string) {
  return useQuery({ queryKey: queryKeys.roundFiles(roundId), queryFn: () => api.listRoundFiles(roundId) });
}

/** One uploaded file's columns for the chosen sheet; disabled until a file is chosen. */
export function useRoundFile(roundId: string, name: string | null, sheet: string | null) {
  return useQuery({
    queryKey: queryKeys.roundFile(roundId, name, sheet),
    queryFn: () => api.getRoundFile(roundId, name as string, sheet),
    enabled: name !== null,
  });
}

export function useUploadRoundFile(roundId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadRoundFile(roundId, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.roundFiles(roundId) }),
  });
}

export function useRemoveRoundFile(roundId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.removeRoundFile(roundId, name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.roundFiles(roundId) }),
  });
}

export function useConfigureRound(roundId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { mapping: Record<string, unknown>; settings: RoundSettings }) => api.configureRound(roundId, body),
    onSuccess: (round) => queryClient.setQueryData(queryKeys.round(roundId), round),
  });
}

export function useRunRound(roundId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.runRound(roundId),
    onSuccess: (round) => queryClient.setQueryData(queryKeys.round(roundId), round),
  });
}

/**
 * One page of a round's review queue. The merge stage's finish time is part of the key, so applying decisions
 * brings in the new queue as soon as the preview is rebuilt, and the last page stays on screen until then.
 */
export function useReviewQueue(round: RoundState, query: ReviewQuery) {
  const mergedAt = round.stages.find((stage) => stage.id === "merge")?.finished_at ?? null;
  return useQuery({
    queryKey: [...queryKeys.review(round.round_id), mergedAt, query],
    queryFn: () => api.getReview(round.round_id, query),
    placeholderData: keepPreviousData,
  });
}

/** After a decision is saved: the round's state and every page of its queue have changed. */
function useRoundChanged(roundId: string) {
  const queryClient = useQueryClient();
  return (round: RoundState) => {
    queryClient.setQueryData(queryKeys.round(roundId), round);
    return queryClient.invalidateQueries({ queryKey: queryKeys.review(roundId) });
  };
}

export function useSaveDecisions(roundId: string) {
  const changed = useRoundChanged(roundId);
  return useMutation({
    mutationFn: (body: DecisionSubmission) => api.saveDecisions(roundId, body),
    onSuccess: changed,
  });
}

export function useDraftDecisions(roundId: string) {
  const changed = useRoundChanged(roundId);
  return useMutation({
    mutationFn: (body: { reviewer: string; create_below: number; reuse_above: number }) => api.draftDecisions(roundId, body),
    onSuccess: (result) => changed(result.round),
  });
}

export function useApplyDecisions(roundId: string) {
  const changed = useRoundChanged(roundId);
  return useMutation({ mutationFn: () => api.applyDecisions(roundId), onSuccess: changed });
}

export function useIntegrateRound(roundId: string) {
  const changed = useRoundChanged(roundId);
  return useMutation({ mutationFn: () => api.integrateRound(roundId), onSuccess: changed });
}

export function useDeleteRound(roundId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteRound(roundId),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: queryKeys.round(roundId) });
      return queryClient.invalidateQueries({ queryKey: queryKeys.rounds, exact: true });
    },
  });
}

/** The enrichment lineage: stages, chains and the runs that scored them. Refetched on focus, not polled. */
export function useLineage() {
  return useQuery({ queryKey: queryKeys.lineage, queryFn: api.getLineage });
}

/**
 * Compare two stored runs (or two taxonomies of one run). Disabled until both run ids are picked.
 * A stored comparison never changes, so it stays fresh for the session.
 */
export function useRunComparison(before: RunSubject | null, after: RunSubject | null) {
  return useQuery({
    queryKey: queryKeys.runComparison(before ?? { runId: "" }, after ?? { runId: "" }),
    queryFn: () => api.compareRuns(before as RunSubject, after as RunSubject),
    enabled: Boolean(before?.runId && after?.runId),
    staleTime: Infinity,
  });
}

/** Several comparisons at once (one per consecutive stage pair), in the order given. */
export function useRunComparisons(pairs: [RunSubject, RunSubject][]) {
  return useQueries({
    queries: pairs.map(([before, after]) => ({
      queryKey: queryKeys.runComparison(before, after),
      queryFn: () => api.compareRuns(before, after),
      staleTime: Infinity,
    })),
  });
}
