"use client";

import { CheckCircle2, Info, Sparkles, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Notice } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Sparkline, TrendChart } from "@/components/viz/trend-chart";
import { VerdictChip } from "@/components/viz/verdict-chip";
import type { LineageRun, RunComparison } from "@/lib/api/schemas";
import { useLineage, useMetricCatalogue, useRunComparisons, useRunSummary } from "@/lib/api/queries";
import { formatChange, formatValue } from "@/lib/format";
import {
  type ChainOption,
  chainOptions,
  comparisonPairs,
  highlights,
  overallChange,
  type ScoredStage,
  scoredStages,
  stageCounts,
  stageLabel,
  type TrendGroup,
  trendGroups,
} from "@/lib/improvement";
import { cn } from "@/lib/utils";

/** Sizes shown on each stage card, in this order when the run reports them. */
const SIZE_TABLES = ["categories", "specialities", "tags"];

export function ImprovementView() {
  const lineage = useLineage();
  const [chainIndex, setChainIndex] = useState(0);
  const [pickedRun, setPickedRun] = useState<string | null>(null);

  const chains = useMemo(() => (lineage.data ? chainOptions(lineage.data) : []), [lineage.data]);

  if (lineage.isPending) return <Skeleton className="h-96 w-full" />;
  if (lineage.error) {
    return (
      <Notice title="Could not load the lineage" tone="error">
        {lineage.error.message}
      </Notice>
    );
  }
  if (chains.length === 0) {
    return (
      <EmptyState title="No enrichment rounds yet">
        Integrated rounds appear here once <code>taxonomy-pipeline integrate</code> has written a snapshot.
      </EmptyState>
    );
  }

  const chain = chains[Math.min(chainIndex, chains.length - 1)];
  const run = chain.runs.find((item) => item.run_id === pickedRun) ?? chain.runs[0] ?? null;

  return (
    <div className="flex flex-col gap-8">
      <Controls
        chains={chains}
        chainIndex={chainIndex}
        onChain={(index) => {
          setChainIndex(index);
          setPickedRun(null);
        }}
        chain={chain}
        run={run}
        onRun={setPickedRun}
      />
      {run ? (
        <ChainReport key={`${chainIndex}-${run.run_id}`} chain={chain} run={run} />
      ) : (
        <>
          <StageStepper chain={chain} scored={null} counts={null} />
          <EmptyState title="No run scores these stages together yet">
            Score every stage in one run (see <code>configs/lineage_scorecard.yaml</code>) to compare them here.
          </EmptyState>
        </>
      )}
    </div>
  );
}

function Controls({
  chains, chainIndex, onChain, chain, run, onRun,
}: {
  chains: ChainOption[];
  chainIndex: number;
  onChain: (index: number) => void;
  chain: ChainOption;
  run: LineageRun | null;
  onRun: (runId: string) => void;
}) {
  const chainName = (option: ChainOption) => option.stages.map((stage) => stage.name).join(" → ");
  return (
    <div className="flex flex-wrap items-end gap-4">
      {chains.length > 1 && (
        <div className="flex flex-col gap-1.5">
          <Label className="label-caps" htmlFor="lineage-chain">Rounds</Label>
          <Select value={String(chainIndex)} onValueChange={(value) => onChain(Number(value))}>
            <SelectTrigger id="lineage-chain" className="w-80">
              <SelectValue>{chainName(chain)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {chains.map((option, index) => (
                <SelectItem key={chainName(option)} value={String(index)}>
                  {chainName(option)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      {chain.runs.length > 0 && run && (
        <div className="flex flex-col gap-1.5">
          <Label className="label-caps" htmlFor="lineage-run">Scored in run</Label>
          <Select value={run.run_id} onValueChange={(value) => value && onRun(value)}>
            <SelectTrigger id="lineage-run" className="w-80">
              <SelectValue>{runName(run, chain)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {chain.runs.map((option) => (
                <SelectItem key={option.run_id} value={option.run_id}>
                  {runName(option, chain)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      {run && (
        <Link href={`/runs/${run.run_id}`} className="pb-2 text-sm text-muted-foreground underline-offset-4 hover:underline">
          Open run {run.run_id.slice(0, 8)}
        </Link>
      )}
      {run && firstAndLast(chain, run) && (
        <Link href={firstAndLast(chain, run) as string} className="pb-2 text-sm text-muted-foreground underline-offset-4 hover:underline">
          First vs latest, side by side
        </Link>
      )}
    </div>
  );
}

/** A link to the Compare page for the run's first and last scored stages. */
function firstAndLast(chain: ChainOption, run: LineageRun): string | null {
  const scored = scoredStages(chain, run);
  if (scored.length < 2) return null;
  const params = new URLSearchParams({
    before: run.run_id, before_subject: scored[0].dataset, after: run.run_id, after_subject: scored[scored.length - 1].dataset,
  });
  return `/compare?${params}`;
}

function runName(run: LineageRun, chain: ChainOption): string {
  const covered = chain.stages.filter((stage) => stage.id in run.datasets).length;
  const date = new Date(run.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return `${run.run_id.slice(0, 8)} · ${date} · ${covered}/${chain.stages.length} stages · judge ${run.judged ? "on" : "off"}`;
}

/** Everything for one chain read through one run: fetched once, laid out top to bottom. */
function ChainReport({ chain, run }: { chain: ChainOption; run: LineageRun }) {
  const scored = useMemo(() => scoredStages(chain, run), [chain, run]);
  const pairs = useMemo(() => comparisonPairs(run.run_id, scored), [run.run_id, scored]);
  const report = useRunSummary(run.run_id);
  const queries = useRunComparisons(pairs.overall ? [...pairs.steps, pairs.overall] : pairs.steps);
  const catalogue = useMetricCatalogue();

  const ready = report.data && queries.every((query) => query.data);
  const failed = report.error ?? queries.find((query) => query.error)?.error;
  const comparisons = queries.map((query) => query.data).filter((data): data is RunComparison => Boolean(data));
  const steps = comparisons.slice(0, pairs.steps.length);
  const overall = pairs.overall ? comparisons[pairs.steps.length] ?? null : null;
  const groups = useMemo(
    () => (ready && report.data ? trendGroups(report.data, scored, steps, overall) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- recompute when the fetched data lands
    [ready, report.data, scored, comparisons.length],
  );
  const xLabels = scored.map((item) => stageLabel(item.position));

  return (
    <>
      <StageStepper chain={chain} scored={scored} counts={report.data ? stageCounts(report.data, scored) : null} />
      {failed && (
        <Notice title="Could not load the comparison" tone="error">
          {failed.message}
        </Notice>
      )}
      {!ready && !failed && (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">Comparing {scored.length} stages…</p>
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-72 w-full" />
        </div>
      )}
      {ready && (
        <>
          <ComparabilityNote comparisons={comparisons} />
          <Highlights groups={groups} />
          {metricSections(groups).map(([metricId, sectionGroups]) => (
            <MetricSection
              key={metricId}
              metricId={metricId}
              description={catalogue.data?.[metricId]?.description}
              groups={sectionGroups}
              xLabels={xLabels}
            />
          ))}
          <ReadingGuide />
        </>
      )}
    </>
  );
}

function StageStepper({
  chain, scored, counts,
}: {
  chain: ChainOption;
  scored: ScoredStage[] | null;
  counts: Record<string, number>[] | null;
}) {
  const sizeAt = (position: number) => {
    const index = scored?.findIndex((item) => item.position === position) ?? -1;
    return index >= 0 && counts ? counts[index] : null;
  };
  const tables = SIZE_TABLES.filter((table) => counts?.some((row) => table in row));
  const largest = Object.fromEntries(tables.map((table) => [table, Math.max(...(counts ?? []).map((row) => row[table] ?? 0))]));

  return (
    <section aria-label="Stages" className="grid gap-3" style={{ gridTemplateColumns: `repeat(${chain.stages.length}, minmax(0, 1fr))` }}>
      {chain.stages.map((stage, position) => {
        const size = sizeAt(position);
        return (
          <Card key={stage.id} size="sm" className="relative">
            <CardHeader>
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{stageLabel(position)}</p>
              <CardTitle className="font-mono text-base">{stage.name}</CardTitle>
              <CardDescription className="flex flex-wrap items-center gap-1.5">
                {stage.parent === null ? (
                  <span>Starting taxonomy</span>
                ) : (
                  <>
                    <span>+</span>
                    {stage.sources.length > 0 ? (
                      stage.sources.map((source) => (
                        <Badge key={source} variant="secondary" className="font-mono">
                          {source}
                        </Badge>
                      ))
                    ) : (
                      <span>unknown source</span>
                    )}
                    {stage.created_at && <span>· {new Date(stage.created_at).toLocaleDateString()}</span>}
                  </>
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-2.5">
              {tables.length === 0 && !size && <p className="text-xs text-muted-foreground">Not scored in this run.</p>}
              {tables.map((table) => {
                const total = size?.[table];
                const added = stage.added[table];
                return (
                  <div key={table} className="flex flex-col gap-1">
                    <div className="flex items-baseline justify-between gap-2 text-sm">
                      <span className="text-muted-foreground capitalize">{table}</span>
                      <span className="tabular-nums">
                        <span className="font-medium">{formatValue(total ?? null, "number")}</span>
                        {added ? <span className="ml-1.5 text-xs text-good">+{formatValue(added, "number")}</span> : null}
                      </span>
                    </div>
                    <div className="flex h-1.5 overflow-hidden rounded-full bg-muted">
                      {total !== undefined && largest[table] > 0 && (
                        <>
                          <div className="h-full bg-viz-1/45" style={{ width: `${((total - (added ?? 0)) / largest[table]) * 100}%` }} />
                          {added ? <div className="ml-0.5 h-full rounded-r-full bg-viz-1" style={{ width: `${(added / largest[table]) * 100}%` }} /> : null}
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        );
      })}
    </section>
  );
}

function ComparabilityNote({ comparisons }: { comparisons: RunComparison[] }) {
  const issues = [...new Set(comparisons.flatMap((comparison) => comparison.comparability.issues.map((issue) => issue.message)))];
  const comparable = comparisons.every((comparison) => comparison.comparable);
  if (comparable) {
    return (
      <p className="flex items-center gap-2 rounded-lg border border-good/30 bg-good/5 px-3 py-2 text-sm">
        <CheckCircle2 className="size-4 text-good" aria-hidden />
        Every stage was scored the same way in this run, so all changes below compare like for like.
      </p>
    );
  }
  return (
    <div className="flex items-start gap-2 rounded-lg border border-bad/30 bg-bad/5 px-3 py-2 text-sm">
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden />
      <div>
        <p>Some results weren&apos;t scored alike, so they show values without a verdict.</p>
        {issues.length > 0 && (
          <ul className="mt-1 list-disc pl-4 text-muted-foreground">
            {issues.map((issue) => <li key={issue}>{issue}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}

function Highlights({ groups }: { groups: TrendGroup[] }) {
  const picked = highlights(groups);
  if (picked.length === 0) return null;
  return (
    <section aria-labelledby="highlights" className="flex flex-col gap-3">
      <h2 id="highlights" className="flex items-center gap-2 text-lg font-semibold">
        <Sparkles className="size-4 text-muted-foreground" aria-hidden />
        Biggest changes, first to latest
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {picked.map(({ group, row }) => {
          const change = overallChange(row);
          const first = row.values.find((value) => value !== null) ?? null;
          const last = row.values.findLast((value) => value !== null) ?? null;
          return (
            <Card key={`${group.key}-${row.key}`} size="sm">
              <CardContent className="flex flex-col gap-2">
                <p className="text-xs text-muted-foreground">
                  <span className="font-medium text-foreground capitalize">{group.metricId}</span>
                  {group.qualifier && <> vs <span className="font-mono">{group.qualifier}</span></>}
                </p>
                <p className="text-sm leading-snug">{row.label}</p>
                <p className="flex items-baseline gap-2">
                  <span className="text-2xl font-semibold tabular-nums">{formatValue(last, row.unit)}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">from {formatValue(first, row.unit)}</span>
                </p>
                <div className="flex items-center justify-between gap-2">
                  <VerdictChip change={change} showLabel />
                  <Sparkline values={row.values} unit={row.unit} labels={row.values.map((_, i) => stageLabel(i))} />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </section>
  );
}

/** Groups by metric, in the order the run lists them. */
function metricSections(groups: TrendGroup[]): [string, TrendGroup[]][] {
  const sections = new Map<string, TrendGroup[]>();
  for (const group of groups) sections.set(group.metricId, [...(sections.get(group.metricId) ?? []), group]);
  return [...sections.entries()];
}

function MetricSection({
  metricId, description, groups, xLabels,
}: {
  metricId: string;
  description?: string;
  groups: TrendGroup[];
  xLabels: string[];
}) {
  return (
    <section aria-labelledby={`metric-${metricId}`} className="flex flex-col gap-3">
      <div>
        <h2 id={`metric-${metricId}`} className="text-lg font-semibold capitalize">{metricId}</h2>
        {description && <p className="max-w-3xl text-sm text-muted-foreground">{description}</p>}
      </div>
      <div className="grid gap-4">
        {groups.map((group) => (
          <GroupCard key={group.key} group={group} xLabels={xLabels} />
        ))}
      </div>
    </section>
  );
}

function GroupCard({ group, xLabels }: { group: TrendGroup; xLabels: string[] }) {
  const rates = group.rows.filter((row) => row.unit === "rate").slice(0, 3);
  const directions = new Set(rates.map((row) => row.better));
  const direction = directions.size === 1 ? [...directions][0] : null;
  const title = group.qualifier ? `${group.metricId} vs ${group.qualifier}` : group.metricId;
  return (
    <Card>
      {group.qualifier && (
        <CardHeader>
          <CardTitle className="text-base">
            Against <span className="font-mono">{group.qualifier}</span>
          </CardTitle>
          {direction === "lower" && (
            <CardDescription>Lower is better: less of {group.qualifier} is missing from the taxonomy.</CardDescription>
          )}
        </CardHeader>
      )}
      <CardContent className={cn("grid gap-6", rates.length > 0 && "lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]")}>
        {rates.length > 0 && (
          <figure className="flex flex-col gap-1">
            <TrendChart
              title={`${title}: ${rates.map((row) => row.label).join(", ")} across ${xLabels.join(", ")}`}
              series={rates.map((row) => ({ label: row.label, values: row.values }))}
              xLabels={xLabels}
              unit="rate"
            />
            {direction && !group.qualifier && (
              <figcaption className="text-xs text-muted-foreground">
                {direction === "higher" ? "Higher is better." : "Lower is better."}
              </figcaption>
            )}
          </figure>
        )}
        <TrendTable group={group} xLabels={xLabels} />
      </CardContent>
      {group.issues.length > 0 && (
        <CardContent>
          <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
            <Info className="mt-0.5 size-3 shrink-0" aria-hidden />
            Not compared: {group.issues.join("; ")}
          </p>
        </CardContent>
      )}
    </Card>
  );
}

function TrendTable({ group, xLabels }: { group: TrendGroup; xLabels: string[] }) {
  const reason = group.issues.join("; ") || undefined;
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Measure</TableHead>
            <TableHead className="hidden md:table-cell">Trend</TableHead>
            {xLabels.map((label) => (
              <TableHead key={label} className="text-right">{label.replace("Baseline ", "B")}</TableHead>
            ))}
            <TableHead className="text-right">Change</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {group.rows.map((row) => (
            <TableRow key={row.key}>
              <TableCell className="max-w-56 whitespace-normal">{row.label}</TableCell>
              <TableCell className="hidden md:table-cell">
                <Sparkline values={row.values} unit={row.unit} labels={xLabels} />
              </TableCell>
              {row.values.map((value, index) => (
                <TableCell key={xLabels[index]} className="text-right tabular-nums">
                  {formatValue(value, row.unit)}
                  {index > 0 && row.steps[index - 1]?.change ? (
                    <span className="block text-[11px] text-muted-foreground">
                      {formatChange(row.steps[index - 1]?.change, row.unit)}
                    </span>
                  ) : null}
                </TableCell>
              ))}
              <TableCell className="text-right">
                <VerdictChip change={overallChange(row)} reason={reason} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function ReadingGuide() {
  return (
    <Card size="sm" className="bg-muted/40">
      <CardContent className="grid gap-3 text-sm text-muted-foreground md:grid-cols-3">
        <p>
          <span className="font-medium text-foreground">Change</span> compares the first stage with the last. Each
          stage&apos;s step change is under its value.
        </p>
        <p>
          <span className="inline-flex items-center gap-1 font-medium text-good">Green</span> is better and{" "}
          <span className="font-medium text-bad">red</span> is worse, in whichever direction that metric counts as
          better. Grey has no better direction or didn&apos;t move.
        </p>
        <p>
          Rates are percentages; their changes are percentage points. Saturation&apos;s new-items rate is a range: the
          low end counts only clear additions, the high end also the concepts needing review.
        </p>
      </CardContent>
    </Card>
  );
}

function EmptyState({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="border-dashed">
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{children}</CardDescription>
      </CardHeader>
    </Card>
  );
}
