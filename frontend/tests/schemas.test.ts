import { describe, expect, it } from "vitest";

import {
  comparabilitySchema,
  existingConceptSchema,
  isRunFinished,
  isTaxonomyDataset,
  pipelineConfigSchema,
  proposalReviewReportSchema,
  reviewDecisionSchema,
  taxonomyLineageSchema,
  reviewDecisionEventSchema,
  runComparisonSchema,
  runReportSchema,
  screeningSettingsSchema,
  tagLookupResultSchema,
  taxonomyConceptDetailSchema,
  taxonomyConceptSummarySchema,
} from "@/lib/api/schemas";

import { lineage } from "./fixtures/improvement";
import { doneRunReport } from "./fixtures/reports";
import { proposalReview } from "./fixtures/proposal-review";

describe("proposalReviewReportSchema", () => {
  it("retains branch evidence, scores and a decision separately from the screening suggestion", () => {
    const item = proposalReviewReportSchema.parse(proposalReview).proposals[0];
    expect(item.proposed_path).toEqual(["Subject", "Computing", "Data visualizatio"]);
    expect(item.proposal.matches).toEqual(proposalReview.proposals[0].proposal.matches);
    expect(item.proposal.suggested_action).toBe("defer");
    expect(item.decision?.action).toBe("reuse");
    expect(item.resolved_node?.id).toBe(20);
  });

  it("rejects invalid match scores instead of silently accepting a drifted API payload", () => {
    const invalid = structuredClone(proposalReview);
    invalid.proposals[0].proposal.matches[0].semantic_score = 1.5;
    expect(proposalReviewReportSchema.safeParse(invalid).success).toBe(false);
  });
});

describe("reviewDecisionSchema", () => {
  it("accepts tag and hierarchy decisions using the same action names", () => {
    const tag = { members: [{ source_dataset: "feed", tag_id: "1" }], action: "create", reason: "Reviewed" };
    expect(reviewDecisionSchema.parse(tag).proposal_id).toBeNull();
    for (const action of ["create", "exclude", "reuse", "defer"]) {
      const hierarchy = { proposal_id: "proposal", action, reason: "Reviewed",
        existing_concept_id: action === "reuse" ? 20 : null };
      expect(reviewDecisionSchema.parse(hierarchy).members).toEqual([]);
      expect(reviewDecisionEventSchema.parse({ event_id: "event", submission_id: "submission",
        recorded_at: "2026-10-18T00:00:00Z", baseline_snapshot_id: "snapshot", decision: hierarchy,
      }).decision.action).toBe(action);
    }
  });

  it("rejects mixed targets, missing merge IDs and blank review reasons", () => {
    const choice = { proposal_id: "proposal", action: "create", reason: "Reviewed" };
    for (const changes of [
      { members: [{ source_dataset: "feed", tag_id: "1" }] }, { existing_tag_id: 30 },
      { action: "reuse" }, { existing_concept_id: 20 }, { reason: " " },
    ]) expect(reviewDecisionSchema.safeParse({ ...choice, ...changes }).success).toBe(false);
  });
});

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

describe("runComparisonSchema", () => {
  // The shape `RunComparison.model_dump(mode="json")` produces for a comparable pair of runs.
  const comparison = {
    comparability: {
      before_run_id: "base", after_run_id: "enriched", before_subject: "original", after_subject: "enriched",
      before_definitions_version: 1, after_definitions_version: 1, issues: [],
      metrics: [{ metric_id: "breadth", before_index: 2, after_index: 2, issues: [] }], comparable: true,
    },
    metrics: [{
      metric_id: "breadth", label: "breadth", before_index: 2, after_index: 2, issues: [],
      measures: [
        { key: "occupational.matched_count", label: "SSOC occupational nodes covered", better: "higher", unit: "number",
          before: 16, after: 24, change: 8, relative_change: 0.5, verdict: "improved" },
        { key: "reviewed", label: "Incoming concepts reviewed", better: null, unit: "number",
          before: 4308, after: null, change: null, relative_change: null, verdict: "not_compared" },
      ],
    }],
    comparable: true,
  };

  it("reads changes and verdicts, and measures one run lacks", () => {
    const parsed = runComparisonSchema.parse(comparison);
    expect(parsed.comparable).toBe(true);
    expect(parsed.metrics[0].measures.map((m) => m.unit)).toEqual(["number", "number"]);
    expect(parsed.metrics[0].measures.map((m) => m.verdict)).toEqual(["improved", "not_compared"]);
  });

  it("rejects a verdict the backend doesn't report", () => {
    const bad = structuredClone(comparison);
    (bad.metrics[0].measures[0] as { verdict: string }).verdict = "better";
    expect(runComparisonSchema.safeParse(bad).success).toBe(false);
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
    expect(withPaths("synonym_set", ["/data/synonyms_v1.json"]).success).toBe(true);
    expect(withPaths("synonym_set", ["/data/a.json", "/data/b.json"]).success).toBe(false);
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

describe("lineage schema", () => {
  it("reads the real GET /lineage shape", () => {
    const parsed = taxonomyLineageSchema.parse(lineage);
    expect(parsed.chains[0].stages).toEqual(["baseline:original", "judged-1015", "onet-1031"]);
    expect(parsed.stages[0].parent).toBeNull();
    expect(parsed.runs.some((run) => run.judged)).toBe(true);
  });

  it("rejects a stage without its added counts", () => {
    const bad = structuredClone(lineage) as { stages: { added?: unknown }[] };
    delete bad.stages[1].added;
    expect(taxonomyLineageSchema.safeParse(bad).success).toBe(false);
  });
});

describe("tagLookupResultSchema", () => {
  const match = {
    id: 10, name: "Tax Advisory", level: "specialities", match_type: "exact",
    lexical_score: 100, semantic_score: null, active: true,
    paths: [{ service: { id: 1, name: "Industry" }, category: null, speciality: { id: 10, name: "Tax Advisory" } }],
  };
  const result = { query: "tax advisory", snapshot: "onet-1031", exists: true, exact: [match], near: [] };

  it("accepts the shape GET /lookup returns", () => {
    expect(tagLookupResultSchema.parse(result)).toEqual(result);
  });

  it("rejects a match type the API doesn't define", () => {
    const drifted = { ...result, exact: [{ ...match, match_type: "fuzzy" }] };
    expect(tagLookupResultSchema.safeParse(drifted).success).toBe(false);
  });

  it("accepts a synonym match naming the synonym it came through", () => {
    const viaSynonym = { ...result, exact: [{ ...match, match_type: "synonym", synonym: "Tax consulting" }] };
    expect(tagLookupResultSchema.parse(viaSynonym).exact[0].synonym).toBe("Tax consulting");
  });
});

describe("screeningSettingsSchema", () => {
  it("defaults synonyms for reports saved before screening applied them", () => {
    expect(screeningSettingsSchema.parse({ lexical_threshold: 0.9, semantic_threshold: 0.7 }).synonyms).toEqual([]);
  });

  it("keeps the synonyms a screening applied, scoped or global", () => {
    const synonym = {
      synonym_id: "s1", term: "Dataviz", normalized_term: "dataviz", term_key: "dataviz", concept_level: "specialities",
      concept_id: "93", concept_name: "Data visualization", scope_level: "categories", scope_id: "37",
    };
    const settings = screeningSettingsSchema.parse({ lexical_threshold: 0.9, semantic_threshold: 0.7, synonyms: [synonym] });
    expect(settings.synonyms).toEqual([synonym]);
  });
});
