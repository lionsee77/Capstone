"use client";

import { ArrowRight, CheckCircle2, TriangleAlert } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Notice } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VerdictChip } from "@/components/viz/verdict-chip";
import type { RunSubject } from "@/lib/api/client";
import type { MetricChange, RunComparison, RunSummary } from "@/lib/api/schemas";
import { hasOlderRuns, RUNS_PAGE_SIZE, useLoadOlderRuns, useRunComparison, useRuns, useRunSummary } from "@/lib/api/queries";
import { formatDateTime, formatValue, shortRunId } from "@/lib/format";
import { runSubjects } from "@/lib/run-view";

type Side = { runId: string; subject: string | null };

/** The picked runs live in the URL (`?before=&before_subject=&after=&after_subject=`), so a comparison can be linked. */
function useSide(prefix: "before" | "after"): [Side, (side: Side) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const side: Side = { runId: params.get(prefix) ?? "", subject: params.get(`${prefix}_subject`) };
  const set = (next: Side) => {
    const updated = new URLSearchParams(params);
    for (const [key, value] of [[prefix, next.runId], [`${prefix}_subject`, next.subject]] as const) {
      if (value) updated.set(key, value);
      else updated.delete(key);
    }
    router.replace(`${pathname}?${updated}`, { scroll: false });
  };
  return [side, set];
}

export function CompareView() {
  const [limit, setLimit] = useState(2 * RUNS_PAGE_SIZE);
  const runs = useRuns(limit);
  const loadOlder = useLoadOlderRuns(setLimit);
  const [before, setBefore] = useSide("before");
  const [after, setAfter] = useSide("after");
  const finished = (runs.data ?? []).filter((run) => run.status === "done");

  const toSubject = (side: Side): RunSubject | null => (side.runId ? { runId: side.runId, subject: side.subject } : null);
  const comparison = useRunComparison(toSubject(before), toSubject(after));

  if (runs.isPending) return <Skeleton className="h-40 w-full" />;
  if (runs.error) {
    return (
      <Notice title="Could not load runs" tone="error">
        {runs.error.message}
      </Notice>
    );
  }
  if (finished.length === 0) return <Notice title="No finished runs to compare yet" />;

  return (
    <div className="flex flex-col gap-6">
      <Card className="bg-muted/50">
        <CardContent className="flex flex-wrap items-end gap-4">
          <SidePicker id="before" title="Before" runs={finished} side={before} onChange={setBefore} />
          <ArrowRight className="mb-2.5 hidden size-4 text-muted-foreground sm:block" aria-hidden />
          <SidePicker id="after" title="After" runs={finished} side={after} onChange={setAfter} />
        </CardContent>
        {/* Outside the listboxes: they only offer the runs loaded so far, older ones need a page fetched first. */}
        {hasOlderRuns(runs.data?.length ?? 0, limit) && (
          <CardContent className="flex flex-col items-start gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-auto px-0"
              disabled={loadOlder.isPending}
              onClick={() => loadOlder.mutate(limit + RUNS_PAGE_SIZE)}
            >
              {loadOlder.isPending ? "Loading…" : "Load older runs"}
            </Button>
            {loadOlder.error && (
              <p className="text-sm text-destructive">Could not load older runs: {loadOlder.error.message}</p>
            )}
          </CardContent>
        )}
      </Card>
      {!before.runId || !after.runId ? (
        <Notice title="Pick a run on each side">The comparison appears once both a before and an after run are chosen.</Notice>
      ) : comparison.isPending ? (
        <Skeleton className="h-72 w-full" />
      ) : comparison.error ? (
        <Notice title="Could not compare these runs" tone="error">
          {comparison.error.message}
        </Notice>
      ) : (
        <ComparisonReport comparison={comparison.data} />
      )}
    </div>
  );
}

function SidePicker({
  id, title, runs, side, onChange,
}: {
  id: string;
  title: string;
  runs: RunSummary[];
  side: Side;
  onChange: (side: Side) => void;
}) {
  const report = useRunSummary(side.runId || null);
  const subjects = report.data ? runSubjects(report.data) : [];
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="flex flex-col gap-1.5">
        <Label className="label-caps" htmlFor={`${id}-run`}>{title}</Label>
        <Select value={side.runId} onValueChange={(value) => onChange({ runId: value ?? "", subject: null })}>
          <SelectTrigger id={`${id}-run`} className="w-64">
            <SelectValue placeholder="Pick a run" />
          </SelectTrigger>
          <SelectContent>
            {runs.map((run) => (
              <SelectItem key={run.run_id} value={run.run_id}>
                {shortRunId(run.run_id)} · {formatDateTime(run.created_at)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {subjects.length > 1 && (
        <div className="flex flex-col gap-1.5">
          <Label className="label-caps" htmlFor={`${id}-subject`}>Taxonomy</Label>
          <Select value={side.subject ?? ""} onValueChange={(value) => onChange({ ...side, subject: value || null })}>
            <SelectTrigger id={`${id}-subject`} className="w-52 font-mono">
              <SelectValue placeholder="Pick one" />
            </SelectTrigger>
            <SelectContent>
              {subjects.map((subject) => (
                <SelectItem key={subject} value={subject} className="font-mono">
                  {subject}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}

function ComparisonReport({ comparison }: { comparison: RunComparison }) {
  const { comparability } = comparison;
  const counts = { improved: 0, deteriorated: 0 };
  for (const metric of comparison.metrics) {
    for (const measure of metric.measures) {
      if (measure.verdict === "improved") counts.improved += 1;
      if (measure.verdict === "deteriorated") counts.deteriorated += 1;
    }
  }
  return (
    <div className="flex flex-col gap-4">
      {comparison.comparable ? (
        <p className="flex items-center gap-2 rounded-lg border border-good/30 bg-good/5 px-3 py-2 text-sm">
          <CheckCircle2 className="size-4 text-good" aria-hidden />
          Like for like: {comparability.before_subject} and {comparability.after_subject} were scored the same way.
          <span className="ml-auto text-muted-foreground tabular-nums">
            {counts.improved} improved · {counts.deteriorated} worse
          </span>
        </p>
      ) : (
        <div className="flex items-start gap-2 rounded-lg border border-bad/30 bg-bad/5 px-3 py-2 text-sm">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden />
          <div>
            <p>Not fully comparable: values without a verdict are shown for reference only.</p>
            <ul className="mt-1 list-disc pl-4 text-muted-foreground">
              {comparability.issues.map((issue) => <li key={issue.message}>{issue.message}</li>)}
            </ul>
          </div>
        </div>
      )}
      {comparison.metrics.map((metric) => (
        <MetricCard key={`${metric.label}-${metric.before_index}-${metric.after_index}`} metric={metric} />
      ))}
    </div>
  );
}

function MetricCard({ metric }: { metric: MetricChange }) {
  const reason = metric.issues.map((issue) => issue.message).join("; ") || undefined;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="capitalize">{metric.metric_id}</CardTitle>
        <CardDescription className="line-clamp-1 font-mono text-xs break-all" title={metric.label}>{metric.label}</CardDescription>
        {reason && <p className="text-xs text-muted-foreground">Not compared: {reason}</p>}
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Measure</TableHead>
              <TableHead className="text-right">Before</TableHead>
              <TableHead className="text-right">After</TableHead>
              <TableHead className="text-right">Change</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {metric.measures.map((measure) => (
              <TableRow key={measure.key}>
                <TableCell className="whitespace-normal">{measure.label}</TableCell>
                <TableCell className="text-right tabular-nums">{formatValue(measure.before, measure.unit)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatValue(measure.after, measure.unit)}</TableCell>
                <TableCell className="text-right">
                  <VerdictChip change={measure} showLabel reason={reason} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
