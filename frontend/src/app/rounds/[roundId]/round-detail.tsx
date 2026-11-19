"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Notice, PageHeader, Section } from "@/components/layout/page-header";
import { RepeatedNames } from "@/components/repeated-names";
import { StatusDot } from "@/components/run-status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeleteRound, useRound, useRoundConfigText, useRunRound } from "@/lib/api/queries";
import type { RoundState } from "@/lib/api/schemas";
import { hasReviewQueue, roundCompareHref, stagesWorthShowing } from "@/lib/review";
import { splitRoundWarnings } from "@/lib/repeated-names";
import { baselineLabel } from "@/lib/round-setup";
import { roundStatusLabel, STAGE_LABELS } from "@/lib/rounds";

import { ReviewPanel } from "./review-panel";

export function RoundDetail({ roundId }: { roundId: string }) {
  const { data: round, isPending, error } = useRound(roundId);

  if (isPending) return <Skeleton className="h-64 w-full max-w-5xl" />;
  if (!round) {
    return (
      <Notice title="Could not load this round" tone="error">
        {error?.message}
      </Notice>
    );
  }
  const showReview = hasReviewQueue(round) || (round.status === "running" && !round.decisions_applied);
  const warnings = splitRoundWarnings(round.warnings);
  return (
    <div className="flex max-w-5xl flex-col gap-8">
      <PageHeader
        eyebrow="Enrichment round"
        title={round.round_id}
        description={`${round.source_name ?? "No source yet"}, built on ${baselineLabel(round.baseline_snapshot)}. ${roundStatusLabel(round.status)}.`}
      />
      <RoundActions round={round} />
      {round.status === "complete" && <RoundResult round={round} />}
      {warnings.other.length > 0 && (
        <Notice title="Worth checking">
          <ul className="flex list-disc flex-col gap-1 pl-4">
            {warnings.other.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Notice>
      )}
      {/* The review panel lists the same names from its live queue, so show them here only without it. */}
      {!showReview && warnings.repeated.length > 0 && (
        <Notice title="Repeated names">
          <RepeatedNames names={warnings.repeated} />
        </Notice>
      )}
      <details key={String(stagesWorthShowing(round))} open={stagesWorthShowing(round)} className="group">
        <summary className="cursor-pointer text-sm font-medium">
          Stages: {round.stages.filter((stage) => stage.status === "done" || stage.status === "skipped").length} of {round.stages.length} done
        </summary>
        <ol className="mt-1 flex flex-col divide-y divide-rule">
          {round.stages.map((stage) => (
            <li key={stage.id} className="flex flex-col gap-1 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-sm font-medium">{STAGE_LABELS[stage.id]}</span>
                <StatusDot status={stage.status} />
              </div>
              {stage.summary && <p className="text-sm text-muted-foreground">{stage.summary}</p>}
              {stage.error && <p className="text-sm text-destructive">{stage.error}</p>}
            </li>
          ))}
        </ol>
      </details>
      {showReview && <ReviewPanel round={round} />}
      <RoundConfig round={round} />
      <RemoveRound round={round} />
    </div>
  );
}

function RoundActions({ round }: { round: RoundState }) {
  const run = useRunRound(round.round_id);
  const setupHref = `/sources/new?round=${encodeURIComponent(round.round_id)}`;
  if (round.status === "setup") {
    return (
      <Notice title="This round needs its source">
        <Link href={setupHref} className="underline underline-offset-4">Add its files and settings</Link>.
      </Notice>
    );
  }
  if (round.status !== "ready" && round.status !== "failed") return null;
  // A preview that refused a decision is fixed by changing the decision, which the review panel offers.
  if (hasReviewQueue(round)) return null;
  return (
    <div className="flex flex-col items-start gap-2">
      <div className="flex items-center gap-3">
        <Button disabled={run.isPending} onClick={() => run.mutate()}>
          {round.status === "failed" ? "Resume round" : "Run round"}
        </Button>
        <Link href={setupHref} className="text-sm underline underline-offset-4">Change its files or settings</Link>
      </div>
      {run.error && <p className="text-sm text-destructive">{run.error.message}</p>}
    </div>
  );
}

function RoundResult({ round }: { round: RoundState }) {
  const compare = roundCompareHref(round);
  const snapshot = round.stages.find((stage) => stage.id === "integrate")?.summary;
  return (
    <Notice title={`The taxonomy now has a new version: ${round.round_id}`}>
      <p>{snapshot}. It is a snapshot beside the earlier versions, and later rounds can build on it. No database has changed.</p>
      <p className="mt-2 flex flex-wrap gap-4">
        {compare && <Link href={compare} className="text-primary underline underline-offset-4">Compare before and after</Link>}
        <Link href="/improvement" className="text-primary underline underline-offset-4">See it on the Improvement page</Link>
      </p>
    </Notice>
  );
}

function RoundConfig({ round }: { round: RoundState }) {
  const [open, setOpen] = useState(false);
  const { data, error } = useRoundConfigText(round.round_id, open);
  if (round.status === "setup") return null;
  return (
    <Section
      title="Round config"
      aside={
        <Button variant="ghost" size="sm" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"}
        </Button>
      }
    >
      {open && error && <p className="text-sm text-destructive">{error.message}</p>}
      {open && data && (
        <pre className="max-h-96 overflow-auto rounded-md bg-muted/50 p-4 font-mono text-xs">{data.config}</pre>
      )}
    </Section>
  );
}

/** Two steps on the page rather than a browser dialog: removing also deletes the round's snapshot. */
function RemoveRound({ round }: { round: RoundState }) {
  const router = useRouter();
  const remove = useDeleteRound(round.round_id);
  const [confirming, setConfirming] = useState(false);
  if (round.status === "running") return null;
  return (
    <Section title="Remove this round">
      <p className="text-sm text-muted-foreground">
        Deletes its uploaded files, merge run and snapshot. Stored scoring runs and audit history stay.
      </p>
      <div className="flex items-center gap-2">
        {confirming ? (
          <>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => remove.mutate(undefined, { onSuccess: () => router.push("/rounds") })}
            >
              Yes, remove {round.round_id}
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
          </>
        ) : (
          <Button variant="outline" onClick={() => setConfirming(true)}>
            Remove round
          </Button>
        )}
      </div>
      {remove.error && <p className="text-sm text-destructive">{remove.error.message}</p>}
    </Section>
  );
}
