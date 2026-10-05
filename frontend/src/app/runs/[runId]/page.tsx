import { RunDetail } from "./run-detail";

export default async function RunPage({ params }: PageProps<"/runs/[runId]">) {
  const { runId } = await params;
  return <RunDetail runId={runId} />;
}
