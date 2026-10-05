/**
 * Client-only draft of a new data-source integration run: the datasets being
 * plugged in and the metrics to validate them with. Lives in zustand (not
 * TanStack Query) because it is local UI state until it is submitted as a
 * `PipelineConfig` via `useCreateRun`.
 */
import { create } from "zustand";

import type { DatasetConfig, MetricConfig, PipelineConfig } from "@/lib/api/schemas";

type SourceDraftState = {
  datasets: DatasetConfig[];
  metrics: MetricConfig[];
  upsertDataset: (dataset: DatasetConfig) => void;
  removeDataset: (name: string) => void;
  toggleMetric: (id: string, defaultParams?: Record<string, unknown>) => void;
  setMetricParams: (id: string, params: Record<string, unknown>) => void;
  toPipelineConfig: () => PipelineConfig;
  reset: () => void;
};

const initialState = { datasets: [], metrics: [] } satisfies Pick<SourceDraftState, "datasets" | "metrics">;

export const useSourceDraftStore = create<SourceDraftState>()((set, get) => ({
  ...initialState,
  upsertDataset: (dataset) =>
    set((state) => ({
      datasets: state.datasets.some((d) => d.name === dataset.name)
        ? state.datasets.map((d) => (d.name === dataset.name ? dataset : d))
        : [...state.datasets, dataset],
    })),
  removeDataset: (name) => set((state) => ({ datasets: state.datasets.filter((d) => d.name !== name) })),
  toggleMetric: (id, defaultParams = {}) =>
    set((state) => ({
      metrics: state.metrics.some((m) => m.id === id)
        ? state.metrics.filter((m) => m.id !== id)
        : [...state.metrics, { id, params: { ...defaultParams } }],
    })),
  setMetricParams: (id, params) =>
    set((state) => ({ metrics: state.metrics.map((m) => (m.id === id ? { ...m, params } : m)) })),
  toPipelineConfig: () => ({ datasets: get().datasets, metrics: get().metrics }),
  reset: () => set(initialState),
}));
