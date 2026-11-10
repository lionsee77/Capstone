import { RoundDetail } from "./round-detail";

export default async function RoundPage({ params }: PageProps<"/rounds/[roundId]">) {
  const { roundId } = await params;
  return <RoundDetail roundId={roundId} />;
}
