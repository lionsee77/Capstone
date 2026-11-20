import { PageHeader } from "@/components/layout/page-header";

import { NewRoundForm } from "./new-round-form";

export default async function NewRoundPage({ searchParams }: PageProps<"/sources/new">) {
  const { round } = await searchParams;
  return (
    <div className="flex max-w-5xl flex-col gap-8">
      <PageHeader
        eyebrow="Enrichment"
        title="New enrichment round"
        description="Bring a new source into the taxonomy: add its files, say how to read them, then run the pipeline."
      />
      <NewRoundForm roundId={typeof round === "string" ? round : null} />
    </div>
  );
}
