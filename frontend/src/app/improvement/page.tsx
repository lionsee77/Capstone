import { PageHeader } from "@/components/layout/page-header";

import { ImprovementView } from "./improvement-view";

export default function ImprovementPage() {
  return (
    <div className="flex max-w-6xl flex-col gap-8">
      <PageHeader
        eyebrow="Lineage"
        title="How the taxonomy improved"
        description="Each enrichment round adds a new source's tags to the taxonomy. This page follows the rounds from the first baseline to the latest and compares every quality metric between them, all scored in one run so each change is like for like."
      />
      <ImprovementView />
    </div>
  );
}
