import type { MetricResult } from "@/lib/api/schemas";

/**
 * A display label per result: the metric id, plus the params that tell repeated
 * runs of it apart, e.g. `integrity (dataset=original)`. Repeats whose params
 * don't differ (or weren't recorded) fall back to their config position, `#<index>`.
 *
 * Mirrors `result_labels` in the pipeline's `pipeline.py`, so the CLI summary and
 * this dashboard name results the same way.
 */
export function metricLabels(results: MetricResult[]): string[] {
  const groups = new Map<string, MetricResult[]>();
  for (const result of results) {
    groups.set(result.metric_id, [...(groups.get(result.metric_id) ?? []), result]);
  }
  return results.map((result, position) => {
    const group = groups.get(result.metric_id) ?? [];
    if (group.length === 1) return result.metric_id;
    const params = group.map((other) => other.params ?? {});
    const keys = [...new Set(params.flatMap((p) => Object.keys(p)))].sort();
    const differing = keys.filter((key) => new Set(params.map((p) => JSON.stringify(p[key]))).size > 1);
    if (differing.length > 0 && result.params) {
      const own = result.params;
      return `${result.metric_id} (${differing.map((key) => `${key}=${paramText(own[key])}`).join(", ")})`;
    }
    return `${result.metric_id} #${result.index ?? position}`;
  });
}

function paramText(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join(",") : String(value);
}
