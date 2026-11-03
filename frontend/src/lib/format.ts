/**
 * How the dashboard writes a measure's numbers. The unit comes from the API (`Measure.unit`), so
 * nothing here guesses from a key: a "rate" is a fraction shown as a percentage, and its change in
 * percentage points; a "number" is a count or a mean, shown as is.
 */
import type { Verdict } from "@/lib/api/schemas";

export type Unit = "number" | "rate";

const NUMBER = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });

/** A value for display: `0.268` (rate) -> `26.8%`, `1248` (number) -> `1,248`, null -> `–`. */
export function formatValue(value: number | null | undefined, unit: Unit): string {
  if (value === null || value === undefined) return "–";
  if (unit === "rate") return `${trimmed(value * 100)}%`;
  return NUMBER.format(value);
}

/** A signed change: `0.18` (rate) -> `+18 pts`, `-6` (number) -> `−6`, `0` -> `0`, null -> `–`. */
export function formatChange(change: number | null | undefined, unit: Unit): string {
  if (change === null || change === undefined) return "–";
  if (change === 0) return unit === "rate" ? "0 pts" : "0";
  const sign = change > 0 ? "+" : "−";
  const size = Math.abs(change);
  if (unit === "rate") return `${sign}${size * 100 < 0.05 ? "<0.1" : trimmed(size * 100)} pts`;
  return `${sign}${NUMBER.format(size)}`;
}

/** One decimal at most, without a trailing `.0`, so 29.0 reads as 29 and 26.83 as 26.8. */
function trimmed(value: number): string {
  return String(Math.round(value * 10) / 10);
}

/** How a verdict reads: its words, and the tone it is drawn in (`good`/`bad` use the status palette). */
export type VerdictTone = "good" | "bad" | "neutral" | "muted";

export function verdictMeta(verdict: Verdict): { label: string; tone: VerdictTone } {
  switch (verdict) {
    case "improved":
      return { label: "Improved", tone: "good" };
    case "deteriorated":
      return { label: "Worse", tone: "bad" };
    case "changed":
      return { label: "Changed", tone: "neutral" };
    case "unchanged":
      return { label: "No change", tone: "neutral" };
    case "not_compared":
      return { label: "Not compared", tone: "muted" };
  }
}

