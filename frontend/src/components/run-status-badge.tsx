import type { MetricResult, RunStatus } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

const MUTED = { dot: "bg-muted-foreground", text: "text-muted-foreground" };
const SUCCESS = { dot: "bg-success", text: "text-success" };
const FAILED = { dot: "bg-destructive", text: "text-destructive" };

// Run statuses and metric result statuses share one look.
const TONES = {
  queued: MUTED,
  running: { dot: "bg-info animate-pulse", text: "text-info" },
  done: SUCCESS,
  failed: FAILED,
  ok: SUCCESS,
  skipped: MUTED,
  error: FAILED,
} as const satisfies Record<RunStatus | MetricResult["status"], { dot: string; text: string }>;

/** A run's or metric result's status as a coloured dot and a small-caps label. */
export function StatusDot({ status, className }: { status: keyof typeof TONES; className?: string }) {
  const tone = TONES[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-mono text-[0.6875rem] font-medium tracking-[0.1em] uppercase",
        tone.text,
        className,
      )}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full", tone.dot)} />
      {status}
    </span>
  );
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return <StatusDot status={status} />;
}
