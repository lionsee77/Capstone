"use client";

import { RunStatusBadge } from "@/components/run-status-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useRun } from "@/lib/api/queries";
import { metricLabels } from "@/lib/metric-labels";

export function RunDetail({ runId }: { runId: string }) {
  const { data: run, isPending, error } = useRun(runId);

  if (isPending) return <Skeleton className="h-64 w-full max-w-5xl" />;
  if (error) return <p className="text-sm text-destructive">Could not load run: {error.message}</p>;

  const labels = metricLabels(run.results);
  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <header className="flex items-center gap-3">
        <h1 className="font-mono text-2xl font-semibold">{run.run_id.slice(0, 8)}</h1>
        <RunStatusBadge status={run.status} />
      </header>
      {run.error && <p className="text-sm text-destructive">{run.error}</p>}
      {run.results.map((result, index) => (
        // The same metric id can run more than once per config, so key on position
        // too, and label repeats by the params that differ.
        <Card key={`${result.metric_id}-${index}`}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 font-mono">
              {labels[index]}
              <Badge variant={result.status === "error" ? "destructive" : "outline"}>{result.status}</Badge>
            </CardTitle>
            <CardDescription>{result.summary}</CardDescription>
          </CardHeader>
          <CardContent>
            {/* TODO: replace with per-metric visualisations (saturation curve, breadth coverage, ...). */}
            <pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 text-xs">
              {JSON.stringify(result.details, null, 2)}
            </pre>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
