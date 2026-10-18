/**
 * Zod mirrors of the taxonomy pipeline's Pydantic models.
 *
 * Source of truth is `backend/taxonomy_pipeline/src/taxonomy_pipeline/models.py`
 * (RunReport, MetricResult) and `config.py` (PipelineConfig). Every response
 * from the API is parsed through these, so a backend shape change fails loudly
 * at the boundary instead of as `undefined` deep inside a component. When a
 * Pydantic model changes, change the matching schema here in the same PR.
 */
import { z } from "zod";

export const runStatusSchema = z.enum(["queued", "running", "done", "failed"]);
export type RunStatus = z.infer<typeof runStatusSchema>;

export const metricStatusSchema = z.enum(["ok", "skipped", "error"]);

export const metricResultSchema = z.object({
  metric_id: z.string(),
  status: metricStatusSchema,
  summary: z.string(),
  // Metric-specific; each metric module documents its own shape.
  details: z.record(z.string(), z.unknown()),
  // Which config entry produced the result (position, and params resolved over
  // the metric's defaults). Null or absent on reports saved before they existed.
  index: z.number().int().nullish(),
  params: z.record(z.string(), z.unknown()).nullish(),
});
export type MetricResult = z.infer<typeof metricResultSchema>;

export const datasetSummarySchema = z.object({
  name: z.string(),
  counts: z.record(z.string(), z.number()),
});
export type DatasetSummary = z.infer<typeof datasetSummarySchema>;

export const runReportSchema = z.object({
  run_id: z.string(),
  created_at: z.string(),
  finished_at: z.string().nullable(),
  status: runStatusSchema,
  datasets: z.array(datasetSummarySchema),
  results: z.array(metricResultSchema),
  error: z.string().nullable(),
});
export type RunReport = z.infer<typeof runReportSchema>;

/** `GET /runs` rows - a summary, not the full report. */
export const runSummarySchema = runReportSchema.pick({ run_id: true, status: true, created_at: true });
export type RunSummary = z.infer<typeof runSummarySchema>;

/** `GET /metrics` - catalogue keyed by metric id. */
export const metricCatalogueSchema = z.record(
  z.string(),
  z.object({
    description: z.string(),
    default_params: z.record(z.string(), z.unknown()),
  }),
);
export type MetricCatalogue = z.infer<typeof metricCatalogueSchema>;

/** Source types registered in `datasets/__init__.py`. */
export const datasetTypeSchema = z.enum(["sql_dump", "ssic", "ssoc"]);
export type DatasetType = z.infer<typeof datasetTypeSchema>;

/** Reference classifications load from a single workbook (`datasets/__init__.py`). */
const SINGLE_PATH_TYPES: ReadonlySet<DatasetType> = new Set(["ssic", "ssoc"]);

export const datasetConfigSchema = z
  .object({
    name: z.string().min(1, "Dataset name is required"),
    type: datasetTypeSchema,
    // The API rejects relative paths - they must exist on the server.
    paths: z
      .array(z.string().startsWith("/", "Paths must be absolute server-side paths"))
      .min(1, "At least one path is required"),
    table_map: z.record(z.string(), z.string()).default({}),
  })
  .refine((dataset) => !SINGLE_PATH_TYPES.has(dataset.type) || dataset.paths.length === 1, {
    message: "SSIC and SSOC datasets take exactly one workbook path",
    path: ["paths"],
  });
export type DatasetConfig = z.infer<typeof datasetConfigSchema>;

export const metricConfigSchema = z.object({
  id: z.string().min(1),
  params: z.record(z.string(), z.unknown()).default({}),
});
export type MetricConfig = z.infer<typeof metricConfigSchema>;

/** `POST /runs` body - same schema as the YAML configs. */
export const pipelineConfigSchema = z.object({
  datasets: z.array(datasetConfigSchema).min(1, "Add at least one dataset"),
  metrics: z.array(metricConfigSchema).min(1, "Pick at least one metric"),
});
export type PipelineConfig = z.infer<typeof pipelineConfigSchema>;

export const createRunResponseSchema = z.object({
  run_id: z.string(),
  status: z.literal("queued"),
});

/** Mirrors proposals/models.py; persisted as resolution.json's optional `screening` field. */
const conceptLevelSchema = z.enum(["category", "speciality"]);
const conceptIdSchema = z.union([z.number().int(), z.string()]).nullable();

export const existingConceptSchema = z.object({
  reference: z.string(),
  level: conceptLevelSchema,
  id: conceptIdSchema,
  name: z.string(),
  path: z.array(z.string().nullable()),
  path_ids: z.array(conceptIdSchema),
  reusable: z.boolean(),
});

export const conceptMatchSchema = z.object({
  concept: existingConceptSchema,
  match_types: z.array(z.enum(["normalized_name", "match_key", "lexical", "semantic"])),
  lexical_score: z.number().min(0).max(1),
  semantic_score: z.number().min(-1).max(1).nullable().default(null),
  same_parent: z.boolean(),
});

export const screenedProposalSchema = z.object({
  proposal_id: z.string(),
  level: conceptLevelSchema,
  name: z.string(),
  path: z.array(z.string()),
  variants: z.array(z.array(z.string())),
  source_tags: z.array(z.tuple([z.string(), z.string()])),
  origins: z.array(z.tuple([z.string(), z.number().int()])),
  matches: z.array(conceptMatchSchema).default([]),
  status: z.enum(["new", "reuse", "review"]),
  existing_id: z.number().int().nullable().default(null),
  blocks_creation: z.boolean().default(false),
  reason: z.string(),
});

export const screeningSettingsSchema = z.object({
  lexical_threshold: z.number().min(0).max(1).default(0.9),
  semantic_threshold: z.number().min(0).max(1).default(0.7),
}).strict();

export const proposalScreeningReportSchema = z.object({
  baseline_snapshot_id: z.string(),
  settings: screeningSettingsSchema,
  semantic_status: z.enum(["disabled", "complete", "failed"]),
  skipped_checks: z.record(z.string(), z.string()).default({}),
  proposals: z.array(screenedProposalSchema),
});
export type ProposalScreeningReport = z.infer<typeof proposalScreeningReportSchema>;

export function isRunFinished(status: RunStatus): boolean {
  return status === "done" || status === "failed";
}

/** The concept tables a taxonomy dataset always reports counts for - absent
 * on a classification reference (ssic/ssoc), which counts its own levels
 * instead. Used to tell `/taxonomy`-searchable datasets apart from ones
 * that aren't. */
const TAXONOMY_CONCEPT_KEYS = ["services", "categories", "specialities", "tags"] as const;
export function isTaxonomyDataset(dataset: DatasetSummary): boolean {
  return TAXONOMY_CONCEPT_KEYS.every((key) => key in dataset.counts);
}

/** Matches `datasets/base.py`'s CONCEPTS, minus `speciality_tags` (a join table, not a level). */
export const conceptLevelSchema = z.enum(["services", "categories", "specialities", "tags"]);
export type ConceptLevel = z.infer<typeof conceptLevelSchema>;

/** A concept id is usually an integer, but a proposed-not-yet-integrated
 * concept can carry a temporary string id (e.g. "skillsfuture:501"). */
const conceptIdSchema = z.union([z.number(), z.string()]);

export const taxonomyConceptRefSchema = z.object({
  id: conceptIdSchema,
  name: z.string(),
});
export type TaxonomyConceptRef = z.infer<typeof taxonomyConceptRefSchema>;

/** `GET /taxonomy/search` result row. */
export const taxonomyConceptSummarySchema = taxonomyConceptRefSchema.extend({
  level: conceptLevelSchema,
});
export type TaxonomyConceptSummary = z.infer<typeof taxonomyConceptSummarySchema>;

/** One parent chain from a concept up to its service; any step can be null (an orphan row). */
export const taxonomyPathSchema = z.object({
  service: taxonomyConceptRefSchema.nullable(),
  category: taxonomyConceptRefSchema.nullable(),
  speciality: taxonomyConceptRefSchema.nullable(),
});
export type TaxonomyPath = z.infer<typeof taxonomyPathSchema>;

/** `GET /taxonomy/{level}/{id}` - a concept's path(s) up, children, and (for a speciality) its tags. */
export const taxonomyConceptDetailSchema = z.object({
  id: conceptIdSchema,
  name: z.string(),
  level: conceptLevelSchema,
  dataset: z.string(),
  paths: z.array(taxonomyPathSchema),
  children: z.array(taxonomyConceptRefSchema),
  tags: z.array(taxonomyConceptRefSchema),
});
export type TaxonomyConceptDetail = z.infer<typeof taxonomyConceptDetailSchema>;
