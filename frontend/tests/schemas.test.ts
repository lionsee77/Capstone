import { describe, expect, it } from "vitest";

import {
  comparabilitySchema,
  existingConceptSchema,
  isRunFinished,
  isTaxonomyDataset,
  pipelineConfigSchema,
  runReportSchema,
  taxonomyConceptDetailSchema,
  taxonomyConceptSummarySchema,
} from "@/lib/api/schemas";

import { doneRunReport } from "./fixtures/reports";

describe("proposal and taxonomy concept schemas", () => {
  it("keeps singular proposal levels and nullable IDs separate from taxonomy search results", () => {
    for (const level of ["category", "speciality"]) {
      const concept = {
        reference: "baseline-row", level, id: null, name: "Unresolved concept",
        path: [null, "Unresolved concept"], path_ids: [null, null], reusable: false,
      };
      expect(existingConceptSchema.parse(concept).id).toBeNull();
      expect(taxonomyConceptSummarySchema.safeParse({ id: 1, name: concept.name, level }).success).toBe(false);
    }
    for (const level of ["services", "categories", "specialities", "tags"]) {
      expect(taxonomyConceptSummarySchema.safeParse({ id: 1, name: "Concept", level }).success).toBe(true);
      expect(taxonomyConceptSummarySchema.safeParse({ id: null, name: "Concept", level }).success).toBe(false);
    }
  });
});

describe("runReportSchema", () => {
  it("accepts a finished report from the API", () => {
    expect(runReportSchema.parse(doneRunReport).results[0].metric_id).toBe("integrity");
  });

  it("reads which config entry produced each result, and accepts reports saved without it", () => {
    const [first] = doneRunReport.results;
    const labelled = { ...doneRunReport, results: [{ ...first, index: 0, params: { dataset: "original" } }] };
    expect(runReportSchema.parse(labelled).results[0]).toMatchObject({ index: 0, params: { dataset: "original" } });
    expect(runReportSchema.parse(doneRunReport).results[0].index).toBeUndefined();
  });

  it("rejects a status the backend does not emit", () => {
    expect(runReportSchema.safeParse({ ...doneRunReport, status: "cancelled" }).success).toBe(false);
  });

  it("reads the metric definitions version, and accepts reports saved before runs were stamped", () => {
    expect(runReportSchema.parse(doneRunReport).metric_definitions_version).toBe(1);
    const legacy: Record<string, unknown> = { ...doneRunReport };
    delete legacy.metric_definitions_version;
    expect(runReportSchema.parse(legacy).metric_definitions_version).toBeUndefined();
    expect(runReportSchema.parse({ ...doneRunReport, metric_definitions_version: null }).metric_definitions_version)
      .toBeNull();
  });
});

describe("comparabilitySchema", () => {
  // The shape `Comparability.model_dump(mode="json")` produces, including the computed `comparable`.
  const check = {
    before_run_id: "a1", after_run_id: "b2", before_subject: "original", after_subject: null,
    before_definitions_version: 1, after_definitions_version: 1,
    issues: [{ kind: "run_status", message: "the after run is failed: boom" }],
    metrics: [{ metric_id: "integrity", before_index: 0, after_index: null, issues: [] }],
    comparable: false,
  };

  it("reads a check, including a failed run with no subject and an unpaired result", () => {
    const parsed = comparabilitySchema.parse(check);
    expect(parsed.after_subject).toBeNull();
    expect(parsed.metrics[0].after_index).toBeNull();
  });

  it("rejects an issue kind the backend doesn't report", () => {
    const bad = { ...check, issues: [{ kind: "typo", message: "x" }] };
    expect(comparabilitySchema.safeParse(bad).success).toBe(false);
  });
});

describe("pipelineConfigSchema", () => {
  const dataset = { name: "skillsfuture", type: "sql_dump", paths: ["/data/sf.sql"] };

  it("defaults table_map and metric params like the Pydantic model", () => {
    const config = pipelineConfigSchema.parse({ datasets: [dataset], metrics: [{ id: "integrity" }] });
    expect(config.datasets[0].table_map).toEqual({});
    expect(config.metrics[0].params).toEqual({});
  });

  it("rejects relative dataset paths, which the API refuses", () => {
    const result = pipelineConfigSchema.safeParse({
      datasets: [{ ...dataset, paths: ["../sf.sql"] }],
      metrics: [{ id: "integrity" }],
    });
    expect(result.success).toBe(false);
  });

  it("requires exactly one workbook path for SSIC and SSOC references", () => {
    const withPaths = (type: string, paths: string[]) =>
      pipelineConfigSchema.safeParse({ datasets: [{ name: type, type, paths }], metrics: [{ id: "breadth" }] });

    expect(withPaths("ssic", ["/data/ssic.xlsx"]).success).toBe(true);
    expect(withPaths("ssoc", ["/data/a.xlsx", "/data/b.xlsx"]).success).toBe(false);
    // SQL dumps legitimately span several files.
    expect(withPaths("sql_dump", ["/data/a.sql", "/data/b.sql"]).success).toBe(true);
  });

  it("requires at least one dataset and one metric", () => {
    expect(pipelineConfigSchema.safeParse({ datasets: [], metrics: [] }).success).toBe(false);
  });

  it("keeps an optional metric definitions pin, which must be a positive version", () => {
    const config = { datasets: [dataset], metrics: [{ id: "integrity" }] };
    expect(pipelineConfigSchema.parse({ ...config, metric_definitions_version: 1 }).metric_definitions_version).toBe(1);
    expect(pipelineConfigSchema.parse(config).metric_definitions_version).toBeUndefined();
    expect(pipelineConfigSchema.safeParse({ ...config, metric_definitions_version: 0 }).success).toBe(false);
  });
});

describe("taxonomyConceptSummarySchema", () => {
  it("accepts a string id, for a proposed-but-not-integrated concept", () => {
    const result = taxonomyConceptSummarySchema.safeParse({ id: "skillsfuture:501", name: "Infocomm", level: "categories" });
    expect(result.success).toBe(true);
  });

  it("rejects a level the API does not report", () => {
    const result = taxonomyConceptSummarySchema.safeParse({ id: 1, name: "x", level: "speciality_tags" });
    expect(result.success).toBe(false);
  });
});

describe("taxonomyConceptDetailSchema", () => {
  const detail = {
    id: 10,
    name: "Tax Advisory",
    level: "specialities",
    dataset: "original",
    paths: [{ service: { id: 1, name: "Industry" }, category: { id: 1, name: "Accounting" }, speciality: { id: 10, name: "Tax Advisory" } }],
    children: [],
    tags: [{ id: 100, name: "tax" }],
  };

  it("accepts a full concept detail", () => {
    expect(taxonomyConceptDetailSchema.parse(detail).tags[0].name).toBe("tax");
  });

  it("requires IDs on concepts and present path steps", () => {
    expect(taxonomyConceptDetailSchema.safeParse({ ...detail, id: null }).success).toBe(false);
    const path = { ...detail.paths[0], service: { id: null, name: "Industry" } };
    expect(taxonomyConceptDetailSchema.safeParse({ ...detail, paths: [path] }).success).toBe(false);
  });

  it("accepts a null path step, for an orphan row", () => {
    const orphan = { ...detail, paths: [{ service: null, category: null, speciality: { id: 12, name: "Orphan" } }] };
    expect(taxonomyConceptDetailSchema.parse(orphan).paths[0].service).toBeNull();
  });

  it("accepts more than one path, for a tag linked to several specialities", () => {
    const tag = {
      ...detail,
      level: "tags",
      paths: [detail.paths[0], { ...detail.paths[0], speciality: { id: 11, name: "Brand Strategy" } }],
    };
    expect(taxonomyConceptDetailSchema.parse(tag).paths).toHaveLength(2);
  });
});

describe("isTaxonomyDataset", () => {
  it("accepts a dataset with every taxonomy concept count", () => {
    const original = { name: "original", counts: { services: 1, categories: 3, specialities: 3, tags: 4 } };
    expect(isTaxonomyDataset(original)).toBe(true);
  });

  it("rejects a classification reference counting its own levels instead", () => {
    const ssic = { name: "ssic", counts: { section: 1, division: 2, group: 3, class: 4, subclass: 5 } };
    expect(isTaxonomyDataset(ssic)).toBe(false);
  });
});

describe("isRunFinished", () => {
  it("treats only done and failed as terminal", () => {
    expect(["queued", "running", "done", "failed"].map((s) => isRunFinished(s as never))).toEqual([
      false,
      false,
      true,
      true,
    ]);
  });
});
