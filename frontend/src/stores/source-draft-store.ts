/**
 * Client-only draft of a new data-source integration run: the datasets being
 * plugged in and the metrics to validate them with. Lives in zustand (not
 * TanStack Query) because it is local UI state until it is submitted as a
 * `PipelineConfig` via `useCreateRun`.
 *
 * Metric entries are addressed by a draft-only `key`, not by metric id: the
 * backend allows the same metric more than once with different params (e.g.
 * `integrity` once per dataset). The key is stripped in `toPipelineConfig`.
 */
import { create } from "zustand";

import type { DatasetConfig, MetricConfig, PipelineConfig } from "@/lib/api/schemas";

export type DraftMetric = MetricConfig & { key: string };

type SourceDraftState = {
  datasets: DatasetConfig[];
  metrics: DraftMetric[];
  upsertDataset: (dataset: DatasetConfig) => void;
  removeDataset: (name: string) => void;
  /** Adds one entry for `id` and returns its key. */
  addMetric: (id: string, params?: Record<string, unknown>) => string;
  removeMetric: (key: string) => void;
  setMetricParams: (key: string, params: Record<string, unknown>) => void;
  toPipelineConfig: () => PipelineConfig;
  reset: () => void;
};

const initialState = { datasets: [], metrics: [] } satisfies Pick<SourceDraftState, "datasets" | "metrics">;

let nextMetricKey = 0;

export const useSourceDraftStore = create<SourceDraftState>()((set, get) => ({
  ...initialState,
  upsertDataset: (dataset) =>
    set((state) => ({
      datasets: state.datasets.some((d) => d.name === dataset.name)
        ? state.datasets.map((d) => (d.name === dataset.name ? dataset : d))
        : [...state.datasets, dataset],
    })),
  removeDataset: (name) => set((state) => ({ datasets: state.datasets.filter((d) => d.name !== name) })),
  addMetric: (id, params = {}) => {
    const key = `metric-${nextMetricKey++}`;
    set((state) => ({ metrics: [...state.metrics, { key, id, params: { ...params } }] }));
    return key;
  },
  removeMetric: (key) => set((state) => ({ metrics: state.metrics.filter((m) => m.key !== key) })),
  setMetricParams: (key, params) =>
    set((state) => ({ metrics: state.metrics.map((m) => (m.key === key ? { ...m, params } : m)) })),
  toPipelineConfig: () => ({
    datasets: get().datasets,
    metrics: get().metrics.map(({ id, params }) => ({ id, params })),
  }),
  reset: () => set(initialState),
}));
