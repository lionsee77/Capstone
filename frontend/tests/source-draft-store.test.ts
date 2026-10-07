import { beforeEach, describe, expect, it } from "vitest";

import { pipelineConfigSchema } from "@/lib/api/schemas";
import { useSourceDraftStore } from "@/stores/source-draft-store";

const dataset = { name: "skillsfuture", type: "sql_dump" as const, paths: ["/data/sf.sql"], table_map: {} };

beforeEach(() => {
  useSourceDraftStore.getState().reset();
});

describe("source draft store", () => {
  it("replaces a dataset with the same name instead of duplicating it", () => {
    const { upsertDataset } = useSourceDraftStore.getState();
    upsertDataset(dataset);
    upsertDataset({ ...dataset, paths: ["/data/sf-v2.sql"] });
    expect(useSourceDraftStore.getState().datasets).toEqual([{ ...dataset, paths: ["/data/sf-v2.sql"] }]);
  });

  it("adds a metric with a copy of its default params and removes it by key", () => {
    const defaults = { threshold: 90 };
    const key = useSourceDraftStore.getState().addMetric("saturation", defaults);
    defaults.threshold = 10;
    expect(useSourceDraftStore.getState().metrics).toEqual([{ key, id: "saturation", params: { threshold: 90 } }]);
    useSourceDraftStore.getState().removeMetric(key);
    expect(useSourceDraftStore.getState().metrics).toEqual([]);
  });

  it("keeps the same metric twice with different params, as the backend allows", () => {
    const { addMetric, setMetricParams, removeMetric } = useSourceDraftStore.getState();
    const first = addMetric("integrity", { dataset: "original" });
    const second = addMetric("integrity", { dataset: "skillsfuture" });

    setMetricParams(second, { dataset: "skillsfuture", fuzzy_threshold: 90 });
    expect(useSourceDraftStore.getState().toPipelineConfig().metrics).toEqual([
      { id: "integrity", params: { dataset: "original" } },
      { id: "integrity", params: { dataset: "skillsfuture", fuzzy_threshold: 90 } },
    ]);


    // The draft keeps its own copy: mutating the caller's object later changes nothing.
    const formState: Record<string, unknown> = { dataset: "original", fuzzy_threshold: 80 };
    setMetricParams(first, formState);
    formState.fuzzy_threshold = 10;
    expect(useSourceDraftStore.getState().metrics[0].params).toEqual({ dataset: "original", fuzzy_threshold: 80 });

    removeMetric(first);
    expect(useSourceDraftStore.getState().metrics.map((m) => m.key)).toEqual([second]);
  });

  it("produces a config the API schema accepts", () => {
    const { upsertDataset, addMetric } = useSourceDraftStore.getState();
    upsertDataset(dataset);
    addMetric("integrity");
    const config = useSourceDraftStore.getState().toPipelineConfig();
    expect(pipelineConfigSchema.safeParse(config).success).toBe(true);
    // Draft-only keys never reach the API.
    expect(config.metrics[0]).not.toHaveProperty("key");
  });
});
