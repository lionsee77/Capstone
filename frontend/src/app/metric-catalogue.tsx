"use client";

import { Notice } from "@/components/layout/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useMetricCatalogue } from "@/lib/api/queries";

export function MetricCatalogue() {
  const { data, isPending, error } = useMetricCatalogue();

  if (isPending) return <Skeleton className="h-40 w-full" />;
  if (error) {
    return (
      <Notice title="Could not reach the pipeline API" tone="error">
        {error.message}. Start it with <code className="font-mono">taxonomy-pipeline serve</code>.
      </Notice>
    );
  }

  return (
    <ol className="divide-y divide-border">
      {Object.entries(data).map(([id, metric], index) => (
        <li key={id} className="grid grid-cols-[2.5rem_1fr] gap-x-4 py-5 md:grid-cols-[2.5rem_12rem_1fr]">
          <span className="font-mono text-sm text-muted-foreground tabular-nums">
            {String(index + 1).padStart(2, "0")}
          </span>
          <p className="font-mono text-sm font-medium text-primary">{id}</p>
          <p className="col-start-2 mt-1 max-w-prose text-sm leading-relaxed text-muted-foreground md:col-start-3 md:mt-0">
            {metric.description}
          </p>
        </li>
      ))}
    </ol>
  );
}
