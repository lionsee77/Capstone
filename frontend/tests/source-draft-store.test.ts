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

  it("toggles a metric on with its default params and off again", () => {
    const { toggleMetric } = useSourceDraftStore.getState();
    toggleMetric("saturation", { threshold: 90 });
    expect(useSourceDraftStore.getState().metrics).toEqual([{ id: "saturation", params: { threshold: 90 } }]);
    toggleMetric("saturation");
    expect(useSourceDraftStore.getState().metrics).toEqual([]);
  });

  it("produces a config the API schema accepts", () => {
    const { upsertDataset, toggleMetric } = useSourceDraftStore.getState();
    upsertDataset(dataset);
    toggleMetric("integrity");
    expect(pipelineConfigSchema.safeParse(useSourceDraftStore.getState().toPipelineConfig()).success).toBe(true);
  });
});
