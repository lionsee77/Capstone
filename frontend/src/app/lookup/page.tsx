import { PageHeader } from "@/components/layout/page-header";

import { TagLookup } from "./tag-lookup";

export default function LookupPage() {
  return (
    <div className="flex max-w-5xl flex-col gap-8">
      <PageHeader
        eyebrow="Explore"
        title="Tag lookup"
        description="Check whether a term already exists in the taxonomy before proposing it."
      />
      <TagLookup />
    </div>
  );
}
