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
});
export type MetricResult = z.infer<typeof metricResultSchema>;

export const datasetSummarySchema = z.object({
  name: z.string(),
  counts: z.record(z.string(), z.number()),
});

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

export function isRunFinished(status: RunStatus): boolean {
  return status === "done" || status === "failed";
}
