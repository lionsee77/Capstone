"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Notice, PageHeader, Section } from "@/components/layout/page-header";
import { StatusDot } from "@/components/run-status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeleteRound, useRound, useRoundConfigText, useRunRound } from "@/lib/api/queries";
import type { RoundState } from "@/lib/api/schemas";
import { roundStatusLabel, STAGE_LABELS } from "@/lib/rounds";

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
  return (
    <div className="flex max-w-5xl flex-col gap-8">
      <PageHeader
        eyebrow="Enrichment round"
        title={round.round_id}
        description={`${round.source_name ?? "No source yet"}, built on ${round.baseline_snapshot}. ${roundStatusLabel(round.status)}.`}
      />
      <RoundActions round={round} />
      {round.warnings.length > 0 && (
        <Notice title="Worth checking">
          <ul className="flex list-disc flex-col gap-1 pl-4">
            {round.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Notice>
      )}
      <Section title="Stages">
        <ol className="flex flex-col divide-y divide-rule">
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
      </Section>
      <RoundConfig round={round} />
      <RemoveRound round={round} />
    </div>
  );
}

function RoundActions({ round }: { round: RoundState }) {
  const run = useRunRound(round.round_id);
  if (round.status === "setup") {
    return (
      <Notice title="This round isn't set up yet">
        Finish it from <Link href="/sources/new" className="underline underline-offset-4">New round</Link>.
      </Notice>
    );
  }
  if (round.status !== "ready" && round.status !== "failed") return null;
  return (
    <div className="flex flex-col items-start gap-2">
      <Button disabled={run.isPending} onClick={() => run.mutate()}>
        {round.status === "failed" ? "Resume round" : "Run round"}
      </Button>
      {run.error && <p className="text-sm text-destructive">{run.error.message}</p>}
    </div>
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
