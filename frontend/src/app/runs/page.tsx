import { PageHeader } from "@/components/layout/page-header";

import { RunsTable } from "./runs-table";

export default function RunsPage() {
  return (
    <div className="flex max-w-5xl flex-col gap-8">
      <PageHeader
        eyebrow="History"
        title="Validation runs"
        description="Every scoring run the pipeline has recorded, newest first."
      />
      <RunsTable />
    </div>
  );
}
