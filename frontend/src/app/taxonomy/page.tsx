import { PageHeader } from "@/components/layout/page-header";

import { TaxonomySearch } from "./taxonomy-search";

export default function TaxonomyPage() {
  return (
    <div className="flex max-w-5xl flex-col gap-8">
      <PageHeader
        eyebrow="Explore"
        title="Taxonomy"
        description="Search concepts from a finished validation run, and inspect how they sit in the hierarchy."
      />
      <TaxonomySearch />
    </div>
  );
}
