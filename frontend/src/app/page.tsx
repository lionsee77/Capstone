import { PageHeader, Section } from "@/components/layout/page-header";

import { MetricCatalogue } from "./metric-catalogue";

export default function OverviewPage() {
  return (
    <div className="flex max-w-5xl flex-col gap-10">
      <PageHeader
        eyebrow="Pipeline"
        title="Overview"
        description="The quality checks the taxonomy validation pipeline scores every run with."
      />
      <Section title="Metrics">
        <MetricCatalogue />
      </Section>
    </div>
  );
}
