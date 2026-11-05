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
  // The metric definitions (formulas and defaults) the run was scored with; only
  // runs with the same version compare. Null or absent on reports saved before
  // runs were stamped, whose definitions are unknown.
  metric_definitions_version: z.number().int().nullish(),
  datasets: z.array(datasetSummarySchema),
  results: z.array(metricResultSchema),
  error: z.string().nullable(),
});
export type RunReport = z.infer<typeof runReportSchema>;

/** Mirrors `ComparabilityIssue`: one reason two runs' numbers don't compare like for like. */
export const comparabilityIssueSchema = z.object({
  kind: z.enum(["run_status", "definitions_version", "missing_result", "result_status", "settings", "reference_data"]),
  message: z.string(),
});
export type ComparabilityIssue = z.infer<typeof comparabilityIssueSchema>;

/** Mirrors `MetricComparison`: a scoring result paired with its counterpart; an index is null when there is none. */
export const metricComparisonSchema = z.object({
  metric_id: z.string(),
  before_index: z.number().int().nullable(),
  after_index: z.number().int().nullable(),
  issues: z.array(comparabilityIssueSchema),
});
export type MetricComparison = z.infer<typeof metricComparisonSchema>;

/**
 * Mirrors `Comparability` (`quality.definitions.check_comparable`): whether two runs were scored alike. A
 * version comparison (#69) shows deltas only for pairs without issues, and none when `issues` isn't empty.
 */
export const comparabilitySchema = z.object({
  before_run_id: z.string(),
  after_run_id: z.string(),
  // Null when that run didn't finish, so it records no scored taxonomy.
  before_subject: z.string().nullable(),
  after_subject: z.string().nullable(),
  before_definitions_version: z.number().int().nullable(),
  after_definitions_version: z.number().int().nullable(),
  issues: z.array(comparabilityIssueSchema),
  metrics: z.array(metricComparisonSchema),
  comparable: z.boolean(),
});
export type Comparability = z.infer<typeof comparabilitySchema>;

/** Mirrors `Direction`: which way a measure is better; null when neither is. */
export const directionSchema = z.enum(["higher", "lower"]);

/** Mirrors `Verdict`. "changed" is a move with no better direction; "not_compared" a pair not scored alike. */
export const verdictSchema = z.enum(["improved", "unchanged", "deteriorated", "changed", "not_compared"]);
export type Verdict = z.infer<typeof verdictSchema>;

/** Mirrors `Unit`: a "rate" is a fraction (show 0.25 as 25%, its change in points); a "number" is shown as is. */
export const unitSchema = z.enum(["number", "rate"]);

/** Mirrors `MeasureChange`: one headline number in both runs. `change` is null unless the pair compares. */
export const measureChangeSchema = z.object({
  key: z.string(),
  label: z.string(),
  better: directionSchema.nullable(),
  unit: unitSchema,
  before: z.number().nullable(),
  after: z.number().nullable(),
  change: z.number().nullable(),
  relative_change: z.number().nullable(),
  verdict: verdictSchema,
});
export type MeasureChange = z.infer<typeof measureChangeSchema>;

/** Mirrors `MetricChange`: one paired scoring result, labelled as the CLI prints it. */
export const metricChangeSchema = z.object({
  metric_id: z.string(),
  label: z.string(),
  before_index: z.number().int().nullable(),
  after_index: z.number().int().nullable(),
  issues: z.array(comparabilityIssueSchema),
  measures: z.array(measureChangeSchema),
});
export type MetricChange = z.infer<typeof metricChangeSchema>;

/**
 * Mirrors `RunComparison` (`GET /runs/{before_id}/compare/{after_id}`, #69): each metric's measures in two runs,
 * with changes and verdicts only where the results were scored alike. Read `comparable`, not
 * `comparability.comparable`: it also counts a result whose stored details couldn't be read.
 */
export const runComparisonSchema = z.object({
  comparability: comparabilitySchema,
  metrics: z.array(metricChangeSchema),
  comparable: z.boolean(),
});
export type RunComparison = z.infer<typeof runComparisonSchema>;

/** Mirrors `LineageStage`: one taxonomy version, a root baseline (`parent` null) or one integrate round's snapshot. */
export const lineageStageSchema = z.object({
  id: z.string(),
  name: z.string(),
  parent: z.string().nullable(),
  sources: z.array(z.string()),
  created_at: z.string().nullable(),
  added: z.record(z.string(), z.number()),
});
export type LineageStage = z.infer<typeof lineageStageSchema>;

/** Mirrors `LineageRun`: a stored run that scored lineage stages, keyed stage id -> its dataset name. */
export const lineageRunSchema = z.object({
  run_id: z.string(),
  created_at: z.string(),
  judged: z.boolean(),
  datasets: z.record(z.string(), z.string()),
});
export type LineageRun = z.infer<typeof lineageRunSchema>;

/** Mirrors `LineageChain`: one root-to-latest path of stage ids, and the runs to read it with, best first. */
export const lineageChainSchema = z.object({
  stages: z.array(z.string()),
  runs: z.array(z.string()),
});
export type LineageChain = z.infer<typeof lineageChainSchema>;

/** Mirrors `TaxonomyLineage` (`GET /lineage`): stages, chains longest first, and runs that scored them. */
export const taxonomyLineageSchema = z.object({
  stages: z.array(lineageStageSchema),
  chains: z.array(lineageChainSchema),
  runs: z.array(lineageRunSchema),
});
export type TaxonomyLineage = z.infer<typeof taxonomyLineageSchema>;

/** Mirrors `RunSummary`: `GET /runs` rows - a summary, not the full report. */
export const runSummarySchema = runReportSchema.pick({ run_id: true, status: true, created_at: true });
export type RunSummary = z.infer<typeof runSummarySchema>;

/** Mirrors `dict[str, MetricInfo]`: `GET /metrics` - catalogue keyed by metric id. */
export const metricCatalogueSchema = z.record(
  z.string(),
  z.object({
    description: z.string(),
    default_params: z.record(z.string(), z.unknown()),
  }),
);
export type MetricCatalogue = z.infer<typeof metricCatalogueSchema>;

/** Source types registered in `taxonomy/__init__.py`. */
export const datasetTypeSchema = z.enum(["sql_dump", "ssic", "ssoc"]);
export type DatasetType = z.infer<typeof datasetTypeSchema>;

/** Reference classifications load from a single workbook (`taxonomy/__init__.py`). */
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
  // Optional pin: the run fails if the pipeline implements another definitions version.
  metric_definitions_version: z.number().int().positive().optional(),
  datasets: z.array(datasetConfigSchema).min(1, "Add at least one dataset"),
  metrics: z.array(metricConfigSchema).min(1, "Pick at least one metric"),
});
export type PipelineConfig = z.infer<typeof pipelineConfigSchema>;

/** Mirrors `RunCreated`: the `POST /runs` response. */
export const createRunResponseSchema = z.object({
  run_id: z.string(),
  status: z.literal("queued"),
});

/** Mirrors enrichment/proposals/models.py; persisted as resolution.json's optional `screening` field. */
const proposalConceptLevelSchema = z.enum(["category", "speciality"]);
const proposalConceptIdSchema = z.union([z.number().int(), z.string()]).nullable();

export const existingConceptSchema = z.object({
  reference: z.string(),
  level: proposalConceptLevelSchema,
  id: proposalConceptIdSchema,
  name: z.string(),
  path: z.array(z.string().nullable()),
  path_ids: z.array(proposalConceptIdSchema),
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
  level: proposalConceptLevelSchema,
  name: z.string(),
  path: z.array(z.string()),
  variants: z.array(z.array(z.string())),
  source_tags: z.array(z.tuple([z.string(), z.string()])),
  origins: z.array(z.tuple([z.string(), z.number().int()])),
  matches: z.array(conceptMatchSchema).default([]),
  status: z.enum(["new", "reuse", "review"]),
  suggested_action: z.enum(["create", "reuse", "defer"]).optional(), // Absent in older saved reports.
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

/** One ReviewDecision contract for tag groups and hierarchy proposals (#57). */
const reviewTextSchema = z.string().regex(/\S/);
const reviewIdSchema = z.number().int().positive();
export const reviewDecisionSchema = z.object({
  members: z.array(z.object({ source_dataset: reviewTextSchema, tag_id: reviewTextSchema })).default([]),
  proposal_id: reviewTextSchema.nullable().default(null),
  action: z.enum(["create", "exclude", "reuse", "defer"]),
  reason: reviewTextSchema,
  existing_tag_id: reviewIdSchema.nullable().default(null),
  existing_concept_id: reviewIdSchema.nullable().default(null),
  approved_speciality_ids: z.array(reviewIdSchema).default([]),
  approved_paths: z.array(z.object({
    service: z.enum(["Industry", "Function", "Subject", "Others"]),
    category: reviewTextSchema, speciality: reviewTextSchema,
  })).default([]),
  canonical_name: reviewTextSchema.nullable().default(null),
}).strict().superRefine((decision, context) => {
  const hasLinks = decision.approved_paths.length > 0 || decision.approved_speciality_ids.length > 0;
  const hierarchy = decision.proposal_id !== null;
  const valid = hierarchy
    ? decision.members.length === 0 && decision.existing_tag_id === null && !hasLinks
      && decision.canonical_name === null
      && (decision.action === "reuse") === (decision.existing_concept_id !== null)
    : decision.members.length > 0 && decision.existing_concept_id === null
      && (decision.action === "reuse") === (decision.existing_tag_id !== null)
      && (!(decision.action === "exclude" || decision.action === "defer") || !hasLinks)
      && (decision.action === "create" || decision.canonical_name === null);
  if (!valid) context.addIssue({ code: "custom", message: "Decision target and fields do not match its action" });
});
export type ReviewDecision = z.infer<typeof reviewDecisionSchema>;

export const reviewDecisionEventSchema = z.object({
  event_id: reviewTextSchema,
  submission_id: reviewTextSchema,
  recorded_at: z.string().datetime({ offset: true }),
  baseline_snapshot_id: reviewTextSchema,
  actor: reviewTextSchema.default("unspecified"),
  event_type: z.literal("review_decision").default("review_decision"),
  decision: reviewDecisionSchema,
}).strict();

export const resolvedHierarchyNodeSchema = z.object({
  level: proposalConceptLevelSchema,
  id: z.union([reviewIdSchema, reviewTextSchema]),
  parent_id: z.union([reviewIdSchema, reviewTextSchema]),
  name: reviewTextSchema,
  path: z.array(z.string()),
  existing: z.boolean(),
});

export const proposalReviewItemSchema = z.object({
  proposal: screenedProposalSchema,
  proposed_path: z.array(z.string()),
  proposed_parent: z.array(z.string()),
  decision: reviewDecisionSchema.nullable().default(null),
  resolved_node: resolvedHierarchyNodeSchema.nullable().default(null),
});

/** Saved evidence and decisions returned identically by the review CLI and API (#58). */
export const proposalReviewReportSchema = z.object({
  baseline_snapshot_id: z.string(),
  settings: screeningSettingsSchema,
  semantic_status: z.enum(["disabled", "complete", "failed"]),
  skipped_checks: z.record(z.string(), z.string()),
  total: z.number().int().nonnegative(),
  offset: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(200),
  proposals: z.array(proposalReviewItemSchema),
});
export type ProposalReviewReport = z.infer<typeof proposalReviewReportSchema>;

/** PB09 audit facts use snapshot-scoped string IDs and provisional pipeline proposal references. */
export const linkedReviewSchema = z.object({
  decision_id: reviewTextSchema,
  decision: z.record(z.string(), z.unknown()),
  review_event_ids: z.array(reviewTextSchema).default([]),
  actor: reviewTextSchema.nullable().default(null),
  occurred_at: z.string().datetime({ offset: true }).nullable().default(null),
}).strict();

export const changeEventSchema = z.object({
  event_id: reviewTextSchema,
  operation_id: reviewTextSchema,
  dataset: reviewTextSchema,
  snapshot_id: reviewTextSchema,
  concept_level: z.enum(["service", "category", "speciality", "tag", "speciality_tag"]),
  concept_id: reviewTextSchema.nullable().default(null),
  concept_ref: reviewTextSchema,
  concept_name: reviewTextSchema,
  action: z.enum(["add", "remove", "merge", "synonym_add", "synonym_remove", "deprecate", "reactivate", "review_decision"]),
  before: z.record(z.string(), z.unknown()).nullable().default(null),
  after: z.record(z.string(), z.unknown()).nullable().default(null),
  reason: reviewTextSchema,
  actor: reviewTextSchema.default("unspecified"),
  occurred_at: z.string().datetime({ offset: true }),
  proposal_ids: z.array(reviewTextSchema).default([]),
  proposal_namespace: reviewTextSchema.default("pipeline"),
  decision_ids: z.array(reviewTextSchema).default([]),
  decision: z.record(z.string(), z.unknown()).nullable().default(null),
  linked_reviews: z.array(linkedReviewSchema).default([]),
  scope: z.enum(["review", "snapshot", "live"]),
}).strict().superRefine((event, context) => {
  if ((event.action === "review_decision") !== (event.scope === "review")) {
    context.addIssue({ code: "custom", message: "Review decisions require review scope", path: ["scope"] });
  }
  if (event.action === "review_decision" && event.decision === null) {
    context.addIssue({ code: "custom", message: "Review decisions require a payload", path: ["decision"] });
  }
});

export const changeHistorySchema = z.object({
  total: z.number().int().nonnegative(),
  offset: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(200),
  changes: z.array(changeEventSchema),
});
export type ChangeHistory = z.infer<typeof changeHistorySchema>;

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

/** Matches `taxonomy/base.py`'s CONCEPTS, minus `speciality_tags` (a join table, not a level). */
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

/** Mirrors models.py's SourceImport: what `import-source` wrote (#117 will return it from the API). */
export const sourceImportSchema = z.object({
  name: z.string(),
  sql_path: z.string(),
  context_path: z.string(),
  counts: z.record(z.string(), z.number().int()),
  round_config: z.string().nullable(),
  warnings: z.array(z.string()),
});
export type SourceImport = z.infer<typeof sourceImportSchema>;
