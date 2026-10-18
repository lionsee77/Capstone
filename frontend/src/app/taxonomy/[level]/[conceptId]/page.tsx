import { ConceptDetail } from "./concept-detail";

export default async function ConceptDetailPage({
  params,
  searchParams,
}: PageProps<"/taxonomy/[level]/[conceptId]">) {
  const { level, conceptId } = await params;
  const { run_id, dataset } = await searchParams;
  return (
    <ConceptDetail
      level={level}
      conceptId={conceptId}
      runId={typeof run_id === "string" ? run_id : undefined}
      dataset={typeof dataset === "string" ? dataset : undefined}
    />
  );
}
