import { notFound } from "next/navigation";

import { conceptLevelSchema } from "@/lib/api/schemas";

import { ConceptDetail } from "./concept-detail";

export default async function ConceptDetailPage({
  params,
  searchParams,
}: PageProps<"/taxonomy/[level]/[conceptId]">) {
  const { level, conceptId } = await params;
  const { run_id, dataset } = await searchParams;
  // Reject an unknown level here, before the client component mounts and fires a request for it.
  const parsedLevel = conceptLevelSchema.safeParse(level);
  if (!parsedLevel.success) notFound();
  return (
    <ConceptDetail
      level={parsedLevel.data}
      conceptId={conceptId}
      runId={typeof run_id === "string" ? run_id : undefined}
      dataset={typeof dataset === "string" ? dataset : undefined}
    />
  );
}
