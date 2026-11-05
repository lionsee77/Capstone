/**
 * A measure's change and the API's verdict on it. The arrow follows the value (up or down) and the
 * colour follows the verdict, so "fewer duplicates" reads as a green down-arrow. The verdict is
 * always written out too, never colour alone.
 */
import { ArrowDownRight, ArrowUpRight, Ban, Minus } from "lucide-react";

import type { MeasureChange } from "@/lib/api/schemas";
import { formatChange, verdictMeta, type VerdictTone } from "@/lib/format";
import { cn } from "@/lib/utils";

const TONES: Record<VerdictTone, string> = {
  good: "border-good/30 bg-good/10 text-good",
  bad: "border-bad/30 bg-bad/10 text-bad",
  neutral: "border-border bg-muted text-muted-foreground",
  muted: "border-dashed border-border text-muted-foreground",
};

export function VerdictChip({
  change,
  showLabel = false,
  className,
  reason,
}: {
  change: MeasureChange | null;
  showLabel?: boolean;
  className?: string;
  /** Why a pair wasn't compared, shown on hover. */
  reason?: string;
}) {
  if (!change) return <span className="text-xs text-muted-foreground">–</span>;
  const { label, tone } = verdictMeta(change.verdict);
  const Icon =
    change.verdict === "not_compared"
      ? Ban
      : change.change === null || change.change === 0
        ? Minus
        : change.change > 0
          ? ArrowUpRight
          : ArrowDownRight;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap tabular-nums",
        TONES[tone],
        className,
      )}
      title={reason ?? `${label}${change.change !== null ? ` (${formatChange(change.change, change.unit)})` : ""}`}
    >
      <Icon className="size-3" aria-hidden />
      {change.verdict === "not_compared" ? "n/a" : formatChange(change.change, change.unit)}
      <span className={cn(!showLabel && "sr-only")}>{showLabel ? `· ${label}` : label}</span>
    </span>
  );
}
