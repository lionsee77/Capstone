/**
 * The improvement page's view model: arranges what `GET /lineage` and `GET /runs/{a}/compare/{b}`
 * return into stages, trend rows and highlights. It computes no metric - every value, change and
 * verdict is the API's; this only lines comparisons of consecutive stages up into one row per
 * measure, so the page can show a whole chain (Baseline 1 -> 2 -> 3) at once.
 */
import type { RunSubject } from "@/lib/api/client";
import type {
  LineageRun,
  LineageStage,
  MeasureChange,
  MetricChange,
  MetricResult,
  RunComparison,
  RunReport,
  TaxonomyLineage,
} from "@/lib/api/schemas";

/** One chain with its stages and runs resolved from ids, in the API's order (runs best first). */
export type ChainOption = { stages: LineageStage[]; runs: LineageRun[] };

export function chainOptions(lineage: TaxonomyLineage): ChainOption[] {
  const stages = new Map(lineage.stages.map((stage) => [stage.id, stage]));
  const runs = new Map(lineage.runs.map((run) => [run.run_id, run]));
  return lineage.chains.map((chain) => ({
    stages: chain.stages.flatMap((id) => stages.get(id) ?? []),
    runs: chain.runs.flatMap((id) => runs.get(id) ?? []),
  }));
}

/** A stage's place in its chain as people say it: the first is Baseline 1. */
export function stageLabel(position: number): string {
  return `Baseline ${position + 1}`;
}

/** A chain's stages the run scored, in chain order, each with the run's dataset name for it. */
export type ScoredStage = { stage: LineageStage; position: number; dataset: string };

export function scoredStages(chain: ChainOption, run: LineageRun): ScoredStage[] {
  return chain.stages.flatMap((stage, position) => {
    const dataset = run.datasets[stage.id];
    return dataset === undefined ? [] : [{ stage, position, dataset }];
  });
}

/** The comparisons a chain needs: each stage against the next, and the first against the last. */
export function comparisonPairs(
  runId: string,
  scored: ScoredStage[],
): { steps: [RunSubject, RunSubject][]; overall: [RunSubject, RunSubject] | null } {
  const subject = (item: ScoredStage): RunSubject => ({ runId, subject: item.dataset });
  const steps = scored.slice(1).map((item, index): [RunSubject, RunSubject] => [subject(scored[index]), subject(item)]);
  const overall: [RunSubject, RunSubject] | null =
    scored.length > 2 ? [subject(scored[0]), subject(scored[scored.length - 1])] : null;
  return { steps, overall };
}

/** One measure across a chain: its value at every stage, each step's change, and first-to-last. */
export type TrendRow = {
  key: string;
  label: string;
  unit: MeasureChange["unit"];
  better: MeasureChange["better"];
  values: (number | null)[];
  steps: (MeasureChange | null)[];
  overall: MeasureChange | null;
};

/**
 * One scoring entry across a chain, e.g. breadth, or saturation against one incoming source.
 * `qualifier` names the datasets that tell it apart from the same metric's other entries.
 */
export type TrendGroup = {
  key: string;
  metricId: string;
  qualifier: string | null;
  rows: TrendRow[];
  issues: string[];
};

/**
 * Line the step comparisons up into groups of rows. A result is matched across comparisons by
 * its metric and its settings with the subject left out (the subject is the one param that names
 * the stage), which is how the API pairs results too.
 */
export function trendGroups(
  report: RunReport,
  scored: ScoredStage[],
  steps: RunComparison[],
  overall: RunComparison | null,
): TrendGroup[] {
  const subjects = new Set(scored.map((item) => item.dataset));
  const datasetNames = new Set(report.datasets.map((dataset) => dataset.name));
  const groups = new Map<string, TrendGroup & { references: string[] }>();
  const rows = new Map<string, TrendRow>();
  const stageCount = steps.length + 1;

  const keyOf = (change: MetricChange, side: "before" | "after"): string | null => {
    const index = side === "before" ? change.before_index : change.after_index;
    const result = index === null ? undefined : report.results[index];
    return result ? `${change.metric_id}|${settingsKey(result, subjects)}` : null;
  };

  steps.forEach((comparison, step) => {
    for (const change of comparison.metrics) {
      const key = keyOf(change, "before") ?? keyOf(change, "after");
      if (key === null) continue;
      const index = change.before_index ?? change.after_index;
      const result = report.results[index as number];
      let group = groups.get(key);
      if (!group) {
        group = { key, metricId: change.metric_id, qualifier: null, rows: [], issues: [],
          references: referencedDatasets(result, subjects, datasetNames) };
        groups.set(key, group);
      }
      for (const issue of change.issues) {
        if (!group.issues.includes(issue.message)) group.issues.push(issue.message);
      }
      for (const measure of change.measures) {
        const rowKey = `${key}|${measure.key}`;
        let row = rows.get(rowKey);
        if (!row) {
          row = { key: measure.key, label: measure.label, unit: measure.unit, better: measure.better,
            values: Array(stageCount).fill(null), steps: Array(steps.length).fill(null), overall: null };
          rows.set(rowKey, row);
          group.rows.push(row);
        }
        if (row.values[step] === null) row.values[step] = measure.before;
        row.values[step + 1] = measure.after;
        row.steps[step] = measure;
      }
    }
  });

  for (const change of overall?.metrics ?? []) {
    const key = keyOf(change, "before") ?? keyOf(change, "after");
    for (const measure of change.measures) {
      const row = key === null ? undefined : rows.get(`${key}|${measure.key}`);
      if (row) row.overall = measure;
    }
  }

  // Name an entry by the datasets it reads only where the same metric has several entries that differ.
  const byMetric = new Map<string, (TrendGroup & { references: string[] })[]>();
  for (const group of groups.values()) byMetric.set(group.metricId, [...(byMetric.get(group.metricId) ?? []), group]);
  for (const siblings of byMetric.values()) {
    if (siblings.length < 2) continue;
    const distinct = new Set(siblings.map((group) => group.references.join(", ")));
    if (distinct.size < 2) continue;
    for (const group of siblings) group.qualifier = group.references.join(", ") || null;
  }
  return [...groups.values()].map((group) => ({
    key: group.key, metricId: group.metricId, qualifier: group.qualifier, rows: group.rows, issues: group.issues,
  }));
}

/** The result's params without its subject, as a stable string. */
function settingsKey(result: MetricResult, subjects: Set<string>): string {
  const params = result.params ?? {};
  const kept = Object.keys(params)
    .sort()
    .filter((key) => !(typeof params[key] === "string" && subjects.has(params[key] as string)))
    .map((key) => [key, params[key]]);
  return JSON.stringify(kept);
}

/** Datasets other than the subject that a result's params name, e.g. saturation's incoming source. */
function referencedDatasets(result: MetricResult, subjects: Set<string>, datasets: Set<string>): string[] {
  const names: string[] = [];
  for (const value of Object.values(result.params ?? {})) {
    for (const item of Array.isArray(value) ? value : [value]) {
      if (typeof item === "string" && datasets.has(item) && !subjects.has(item) && !names.includes(item)) {
        names.push(item);
      }
    }
  }
  return names;
}

/**
 * The headline changes for the tiles: rate measures with a better direction that moved from first
 * to last stage, improvements first, then the largest moves. Rates compare across metrics (all are
 * shares); counts grow with the taxonomy, so they stay in the table.
 */
export function highlights(groups: TrendGroup[], limit = 8): { group: TrendGroup; row: TrendRow }[] {
  const moved = groups.flatMap((group) =>
    group.rows
      .filter((row) => row.unit === "rate" && row.better !== null)
      .map((row) => ({ group, row, change: overallChange(row) }))
      .filter((item) => item.change !== null && item.change.verdict !== "unchanged"),
  );
  const rank = (verdict: string) => (verdict === "improved" ? 0 : verdict === "deteriorated" ? 1 : 2);
  return moved
    .sort((a, b) => rank(a.change!.verdict) - rank(b.change!.verdict) ||
      Math.abs(b.change!.change ?? 0) - Math.abs(a.change!.change ?? 0))
    .slice(0, limit)
    .map(({ group, row }) => ({ group, row }));
}

/** First-to-last change: the overall comparison, or the only step when the chain has two stages. */
export function overallChange(row: TrendRow): MeasureChange | null {
  return row.overall ?? (row.steps.length === 1 ? row.steps[0] : null);
}

/** Taxonomy size at each scored stage, from the run's own dataset counts. */
export function stageCounts(report: RunReport, scored: ScoredStage[]): Record<string, number>[] {
  return scored.map((item) => report.datasets.find((dataset) => dataset.name === item.dataset)?.counts ?? {});
}
