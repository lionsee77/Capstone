"use client";

import { CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMetricCatalogue } from "@/lib/api/queries";

export function MetricCatalogue() {
  const { data, isPending, error } = useMetricCatalogue();

  if (isPending) return <CardContent><Skeleton className="h-24 w-full" /></CardContent>;
  if (error) return <CardContent className="text-sm text-destructive">Could not reach the pipeline API: {error.message}</CardContent>;

  return (
    <CardContent className="flex flex-col gap-3">
      {Object.entries(data).map(([id, metric]) => (
        <div key={id}>
          <p className="font-mono text-sm font-medium">{id}</p>
          <p className="text-sm text-muted-foreground">{metric.description}</p>
        </div>
      ))}
    </CardContent>
  );
}
