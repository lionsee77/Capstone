import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { MetricCatalogue } from "./metric-catalogue";

export default function OverviewPage() {
  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold">Overview</h1>
        <p className="text-sm text-muted-foreground">
          Quality checks available from the taxonomy validation pipeline.
        </p>
      </header>
      <Card>
        <CardHeader>
          <CardTitle>Metrics</CardTitle>
          <CardDescription>Served live by `GET /metrics`.</CardDescription>
        </CardHeader>
        <MetricCatalogue />
      </Card>
    </div>
  );
}
