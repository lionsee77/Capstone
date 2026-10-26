import type { MetricResult } from "@/lib/api/schemas";

/**
 * A display label per result: the metric id, plus the params that tell repeated
 * runs of it apart, e.g. `integrity (dataset=original)`. Repeats those params
 * still don't tell apart (identical or unrecorded params) get their config
 * position too, `#<index>`, so every label in a report is unique.
 *
 * Mirrors `result_labels` in the pipeline's `app/scoring.py`, so the CLI summary and
 * this dashboard name results the same way; keep the two in step.
 */
export function metricLabels(results: MetricResult[]): string[] {
  const groups = new Map<string, MetricResult[]>();
  for (const result of results) {
    groups.set(result.metric_id, [...(groups.get(result.metric_id) ?? []), result]);
  }
  const labels = results.map((result) => {
    const group = groups.get(result.metric_id) ?? [];
    const params = group.map((other) => other.params ?? {});
    const keys = [...new Set(params.flatMap((p) => Object.keys(p)))].sort();
    const differing = keys.filter((key) => new Set(params.map((p) => paramText(p[key]))).size > 1);
    if (group.length > 1 && differing.length > 0 && result.params) {
      const own = result.params;
      return `${result.metric_id} (${differing.map((key) => `${key}=${paramText(own[key])}`).join(", ")})`;
    }
    return result.metric_id;
  });
  const counts = new Map<string, number>();
  for (const label of labels) counts.set(label, (counts.get(label) ?? 0) + 1);
  return labels.map((label, position) =>
    (counts.get(label) ?? 0) > 1 ? `${label} #${results[position].index ?? position}` : label,
  );
}

/**
 * One param value as label text, in the same format as the pipeline's `_param_text`:
 * JSON-like literals, top-level lists comma-joined, and objects with their keys sorted.
 */
function paramText(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return value.map(paramText).join(",");
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${key}:${paramText(record[key])}`)
      .join(",")}}`;
  }
  return String(value);
}
