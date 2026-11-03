/**
 * Which taxonomies a run scored, read from the run itself: the datasets a scoring result names as
 * its subject. A subject is a plain string param (integrity's `dataset`, breadth's `target`,
 * saturation's `baseline`); sources a result only reads, like saturation's `incoming`, are lists,
 * and reference workbooks aren't taxonomies, so neither counts.
 */
import { isTaxonomyDataset, type RunReport } from "@/lib/api/schemas";

export function runSubjects(report: RunReport): string[] {
  const named = new Set(
    report.results.flatMap((result) => Object.values(result.params ?? {}).filter((value) => typeof value === "string")),
  );
  return report.datasets.filter((dataset) => isTaxonomyDataset(dataset) && named.has(dataset.name)).map((d) => d.name);
}
