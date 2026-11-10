/** Words for a round's progress. The state itself is entirely the API's (`app/rounds.py`). */
import type { RoundState, RoundStageId } from "@/lib/api/schemas";

export const STAGE_LABELS: Record<RoundStageId, string> = {
  import: "Import the source",
  score: "Score against the baseline",
  case_a: "Place tags with a parent",
  case_b: "Classify tags without a parent (LLM)",
  merge: "Build the merge preview",
  integrate: "Generate the enriched snapshot",
  score_enriched: "Score the enriched taxonomy",
};

const STATUS_LABELS: Record<RoundState["status"], string> = {
  setup: "Waiting for its source and config",
  ready: "Ready to run",
  running: "Running",
  failed: "Stopped on an error",
  review: "Waiting for review",
  ready_to_integrate: "Ready to integrate",
  complete: "Complete",
};

export function roundStatusLabel(status: RoundState["status"]): string {
  return STATUS_LABELS[status];
}

export const isRoundActive = (round: RoundState | undefined): boolean => round?.status === "running";
