"use client";

/**
 * A small line chart of one to three measures across ordered points (lineage stages), drawn in
 * plain SVG to the dataviz mark specs: 2px lines, 8px markers with a surface ring, a recessive
 * grid, direct labels at the line ends (so identity is never colour alone), and a crosshair with
 * a tooltip on hover. Values are drawn as the API gave them; only the unit decides the format.
 */
import { useId, useState } from "react";

import { formatValue, type Unit } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Categorical slots in fixed order (validated all-pairs for up to three series). */
const SERIES_COLORS = ["var(--viz-series-1)", "var(--viz-series-2)", "var(--viz-series-3)"];

export type TrendSeries = { label: string; values: (number | null)[] };

type Props = {
  series: TrendSeries[];
  xLabels: string[];
  unit: Unit;
  height?: number;
  className?: string;
  /** Accessible description of what the chart shows. */
  title: string;
};

const PAD = { top: 12, right: 64, bottom: 26, left: 44 };
const WIDTH = 420;

export function TrendChart({ series, xLabels, unit, height = 180, className, title }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();
  const shown = series.slice(0, SERIES_COLORS.length);
  const all = shown.flatMap((s) => s.values).filter((v): v is number => v !== null);
  if (all.length === 0) return <p className="text-sm text-muted-foreground">No values to chart.</p>;

  const [low, high] = domain(all, unit);
  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (xLabels.length === 1 ? plotW / 2 : (i * plotW) / (xLabels.length - 1));
  const y = (v: number) => PAD.top + plotH - ((v - low) / (high - low || 1)) * plotH;
  const ticks = [low, (low + high) / 2, high];
  const endLabels = spreadLabels(
    shown.map((s) => {
      const lastIndex = s.values.findLastIndex((v) => v !== null);
      return lastIndex < 0 ? null : { x: x(lastIndex), y: y(s.values[lastIndex] as number), value: s.values[lastIndex] };
    }),
    PAD.top,
    PAD.top + plotH,
  );

  const onMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * WIDTH;
    let nearest = 0;
    xLabels.forEach((_, i) => {
      if (Math.abs(x(i) - px) < Math.abs(x(nearest) - px)) nearest = i;
    });
    setHover(nearest);
  };

  return (
    <div className={cn("relative", className)}>
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        className="h-auto w-full overflow-visible"
        role="img"
        aria-labelledby={titleId}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        <title id={titleId}>{title}</title>
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={PAD.left + plotW} y1={y(tick)} y2={y(tick)} className="stroke-border" strokeWidth={1} />
            <text x={PAD.left - 6} y={y(tick)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[10px] tabular-nums">
              {formatValue(tick, unit)}
            </text>
          </g>
        ))}
        {xLabels.map((label, i) => (
          <text key={label} x={x(i)} y={height - 6} textAnchor="middle" className="fill-muted-foreground text-[10px]">
            {label}
          </text>
        ))}
        {hover !== null && (
          <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotH} className="stroke-muted-foreground/50" strokeWidth={1} strokeDasharray="3 3" />
        )}
        {shown.map((s, index) => {
          const color = SERIES_COLORS[index];
          const points = s.values.map((v, i) => (v === null ? null : ([x(i), y(v)] as const)));
          const path = points
            .map((point, i) => (point ? `${i === 0 || !points[i - 1] ? "M" : "L"}${point[0]},${point[1]}` : ""))
            .join("");
          const end = endLabels[index];
          const tag = shown.length > 1 ? shortLabel(s.label) : null;
          return (
            <g key={s.label}>
              <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {points.map((point, i) =>
                point ? (
                  <circle key={i} cx={point[0]} cy={point[1]} r={hover === i ? 5 : 4} fill={color} className="stroke-card" strokeWidth={2} />
                ) : null,
              )}
              {end && (
                <text x={end.x + 10} y={end.labelY} dy="0.32em" className="fill-foreground text-[10px]">
                  <tspan className="font-medium tabular-nums">{formatValue(end.value, unit)}</tspan>
                  {tag && <tspan className="fill-muted-foreground"> {tag}</tspan>}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {shown.length > 1 && (
        <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {shown.map((s, index) => (
            <li key={s.label} className="flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-3 rounded-full" style={{ background: SERIES_COLORS[index] }} />
              {s.label}
            </li>
          ))}
        </ul>
      )}
      {hover !== null && (
        <div
          className="pointer-events-none absolute top-0 z-10 rounded-md border bg-popover px-2.5 py-1.5 text-xs shadow-md"
          style={{ left: `${(x(hover) / WIDTH) * 100}%`, transform: `translateX(${hover === xLabels.length - 1 ? "-100%" : hover === 0 ? "0" : "-50%"})` }}
        >
          <p className="font-medium">{xLabels[hover]}</p>
          {shown.map((s, index) => (
            <p key={s.label} className="flex items-center gap-1.5 text-muted-foreground">
              <span className="inline-block size-2 rounded-full" style={{ background: SERIES_COLORS[index] }} />
              {s.label}: <span className="font-medium text-foreground tabular-nums">{formatValue(s.values[hover], unit)}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/** A compact line of one measure across stages, for table rows; hover shows each value. */
export function Sparkline({ values, unit, labels }: { values: (number | null)[]; unit: Unit; labels: string[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const known = values.filter((v): v is number => v !== null);
  if (known.length < 2) return null;
  const width = 72;
  const height = 22;
  const [low, high] = [Math.min(...known), Math.max(...known)];
  const x = (i: number) => 4 + (i * (width - 8)) / (values.length - 1);
  const y = (v: number) => 4 + (height - 8) * (1 - (v - low) / (high - low || 1));
  const path = values
    .map((v, i) => (v === null ? "" : `${i === 0 || values[i - 1] === null ? "M" : "L"}${x(i)},${y(v)}`))
    .join("");
  return (
    <span className="relative inline-block align-middle">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={values.map((v, i) => `${labels[i]} ${formatValue(v, unit)}`).join(", ")}>
        <path d={path} fill="none" stroke="var(--viz-series-1)" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
        {values.map((v, i) =>
          v === null ? null : (
            <circle
              key={i}
              cx={x(i)}
              cy={y(v)}
              r={hover === i ? 3 : 2}
              fill="var(--viz-series-1)"
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
            />
          ),
        )}
        {/* Larger invisible hit targets than the marks. */}
        {values.map((_, i) => (
          <rect key={`hit-${i}`} x={x(i) - 8} y={0} width={16} height={height} fill="transparent" onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)} />
        ))}
      </svg>
      {hover !== null && (
        <span className="pointer-events-none absolute top-1/2 right-full z-10 mr-1 -translate-y-1/2 rounded border bg-popover px-1.5 py-0.5 text-[11px] whitespace-nowrap shadow-sm">
          {labels[hover]}: <span className="font-medium tabular-nums">{formatValue(values[hover], unit)}</span>
        </span>
      )}
    </span>
  );
}

/** A 0-100% bar for one rate, e.g. coverage; the value is always printed beside it. */
export function Meter({ value, label, detail }: { value: number | null; label: string; detail?: string }) {
  const share = value === null ? 0 : Math.max(0, Math.min(1, value));
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span>{label}</span>
        <span className="font-medium tabular-nums">{formatValue(value, "rate")}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)} aria-label={label}>
        <div className="h-full rounded-full bg-viz-1" style={{ width: `${share * 100}%` }} />
      </div>
      {detail && <span className="text-xs text-muted-foreground">{detail}</span>}
    </div>
  );
}

/** Rates sit on 0-100% when they fill it reasonably, else zoom to their range; counts zoom too. */
function domain(values: number[], unit: Unit): [number, number] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (unit === "rate") {
    const top = Math.min(1, Math.ceil((max + 0.05) * 10) / 10);
    return [0, Math.max(top, 0.1)];
  }
  const pad = (max - min || Math.abs(max) || 1) * 0.15;
  return [Math.max(0, min - pad), max + pad];
}

/**
 * "New-items rate (min)" -> "min", so an end label stays short; a label without a parenthetical
 * gets no tag (the legend below names every series, and the colour dot links them).
 */
function shortLabel(label: string): string | null {
  const match = label.match(/\(([^)]+)\)$/);
  return match && match[1].length <= 8 ? match[1] : null;
}

type EndLabel = { x: number; y: number; value: number | null };

/** Push end labels apart vertically (at least one line) so lines ending close together stay readable. */
function spreadLabels(labels: (EndLabel | null)[], top: number, bottom: number): ((EndLabel & { labelY: number }) | null)[] {
  const GAP = 12;
  const placed = labels
    .map((label, index) => (label ? { ...label, labelY: label.y, index } : null))
    .filter((label): label is EndLabel & { labelY: number; index: number } => label !== null)
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < placed.length; i++) {
    placed[i].labelY = Math.max(placed[i].labelY, placed[i - 1].labelY + GAP);
  }
  const overflow = placed.length ? placed[placed.length - 1].labelY - bottom : 0;
  if (overflow > 0) for (const label of placed) label.labelY = Math.max(top, label.labelY - overflow);
  const byIndex = new Map(placed.map((label) => [label.index, label]));
  return labels.map((_, index) => byIndex.get(index) ?? null);
}
