"use client";

import { ChevronDown } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Notice, PageHeader, Section } from "@/components/layout/page-header";
import { RunStatusBadge, StatusDot } from "@/components/run-status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Meter } from "@/components/viz/trend-chart";
import type { MeasureChange, MetricChange, MetricResult, RunReport } from "@/lib/api/schemas";
import { isRunFinished } from "@/lib/api/schemas";
import { useRun, useRunComparison, useRunSummary } from "@/lib/api/queries";
import { formatDateTime, formatValue, shortRunId } from "@/lib/format";
import { metricLabels } from "@/lib/metric-labels";
import { runSubjects } from "@/lib/run-view";
import { cn } from "@/lib/utils";

/**
 * One run, readable. Values come from the comparison API: comparing a taxonomy with itself returns
 * each metric's measures (labels, units, better direction) without any metric logic here. Result
 * details are only fetched when someone asks for the raw JSON, since a judged run's are megabytes.
 */
export function RunDetail({ runId }: { runId: string }) {
  const { data: run, isPending, error } = useRunSummary(runId);

  if (isPending) return <Skeleton className="h-64 w-full max-w-5xl" />;
  if (error) {
    return (
      <Notice title="Could not load run" tone="error">
        {error.message}
      </Notice>
    );
  }

  const subjects = runSubjects(run);
  const finished = run.status === "done";
  return (
    <div className="flex max-w-6xl flex-col gap-8">
      <div className="flex flex-col gap-4">
        <Link href="/runs" className="text-sm text-muted-foreground hover:text-foreground">
          &larr; All runs
        </Link>
        <PageHeader
          eyebrow="Validation run"
          title={shortRunId(run.run_id)}
          titleClassName="font-mono tracking-normal"
          actions={<RunStatusBadge status={run.status} />}
          description={
            <>
              Started {formatDateTime(run.created_at)}
              {run.finished_at && <> · finished {formatDateTime(run.finished_at)}</>}
              {run.metric_definitions_version != null && <> · metric definitions v{run.metric_definitions_version}</>}
            </>
          }
        />
        <DatasetChips run={run} subjects={subjects} />
      </div>
      {run.error && (
        <Notice title="The run failed" tone="error">
          {run.error}
        </Notice>
      )}
      {!isRunFinished(run.status) && <Notice title="Scoring…">This page updates when the run finishes.</Notice>}
      {finished && subjects.length > 1 && (
        <Tabs defaultValue={subjects[0]}>
          <TabsList>
            {subjects.map((subject) => (
              <TabsTrigger key={subject} value={subject} className="font-mono">
                {subject}
              </TabsTrigger>
            ))}
          </TabsList>
          {subjects.map((subject) => (
            <TabsContent key={subject} value={subject} className="mt-4">
              <SubjectResults run={run} subject={subject} />
            </TabsContent>
          ))}
        </Tabs>
      )}
      {finished && subjects.length === 1 && <SubjectResults run={run} subject={subjects[0]} />}
      {finished && <OtherResults run={run} subjects={subjects} />}
    </div>
  );
}

function DatasetChips({ run, subjects }: { run: RunReport; subjects: string[] }) {
  return (
    <ul className="flex flex-wrap gap-2">
      {run.datasets.map((dataset) => (
        <li
          key={dataset.name}
          className={cn("rounded-md border border-rule px-2.5 py-1.5 text-xs", subjects.includes(dataset.name) && "border-primary/40 bg-accent/60")}
        >
          <span className="font-mono font-medium">{dataset.name}</span>
          <span className="ml-2 text-muted-foreground tabular-nums">
            {Object.entries(dataset.counts)
              .filter(([key]) => key !== "speciality_tags")
              .map(([key, count]) => `${formatValue(count, "number")} ${key}`)
              .join(" · ")}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Every metric scoring one taxonomy, as measures from comparing it with itself. */
function SubjectResults({ run, subject }: { run: RunReport; subject: string }) {
  const side = { runId: run.run_id, subject };
  const { data, isPending, error } = useRunComparison(side, side);
  if (isPending) return <Skeleton className="h-48 w-full" />;
  if (error) {
    return (
      <Notice title={`Could not read ${subject}'s measures`} tone="error">
        {error.message}
      </Notice>
    );
  }
  const labels = metricLabels(run.results);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {data.metrics.map((metric) => {
        const index = metric.before_index ?? metric.after_index;
        const result = index === null ? undefined : run.results[index];
        return result && index !== null ? (
          <ResultCard key={index} result={result} label={labels[index]} metric={metric} runId={run.run_id} index={index} />
        ) : null;
      })}
    </div>
  );
}

/** Results scoring no subject, or that didn't run: shown with their summary, so nothing in the run is hidden. */
function OtherResults({ run, subjects }: { run: RunReport; subjects: string[] }) {
  const labels = metricLabels(run.results);
  const scoresSubject = (result: MetricResult) =>
    Object.values(result.params ?? {}).some((value) => typeof value === "string" && subjects.includes(value));
  const rest = run.results
    .map((result, index) => ({ result, index }))
    .filter(({ result }) => result.status !== "ok" || !scoresSubject(result));
  if (rest.length === 0) return null;
  return (
    <Section title="Other results">
      <div className="grid gap-4 lg:grid-cols-2">
        {rest.map(({ result, index }) => (
          <ResultCard key={index} result={result} label={labels[index]} metric={null} runId={run.run_id} index={index} />
        ))}
      </div>
    </Section>
  );
}

function ResultCard({
  result, label, metric, runId, index,
}: {
  result: MetricResult;
  label: string;
  metric: MetricChange | null;
  runId: string;
  index: number;
}) {
  const measures = metric?.measures ?? [];
  const rates = measures.filter((measure) => measure.unit === "rate");
  const numbers = measures.filter((measure) => measure.unit === "number");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3 text-lg">
          <span className="capitalize">{result.metric_id}</span>
          <StatusDot status={result.status} />
        </CardTitle>
        <CardDescription className="line-clamp-1 font-mono text-xs break-all" title={label}>{label}</CardDescription>
        <p className="text-sm">{result.summary}</p>
      </CardHeader>
      {measures.length > 0 && (
        <CardContent className="flex flex-col gap-4">
          {rates.length > 0 && (
            <div className="flex flex-col gap-3">
              {rates.map((measure) => (
                <Meter key={measure.key} value={measure.before} label={measure.label} detail={directionText(measure)} />
              ))}
            </div>
          )}
          {numbers.length > 0 && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
              {numbers.map((measure) => (
                <div key={measure.key} className="flex flex-col">
                  <dt className="text-xs text-muted-foreground">{measure.label}</dt>
                  <dd className="font-heading text-2xl tabular-nums">{formatValue(measure.before, "number")}</dd>
                </div>
              ))}
            </dl>
          )}
        </CardContent>
      )}
      <CardContent>
        <RawDetails runId={runId} index={index} />
      </CardContent>
    </Card>
  );
}

function directionText(measure: MeasureChange): string | undefined {
  if (measure.better === "higher") return "Higher is better";
  if (measure.better === "lower") return "Lower is better";
  return undefined;
}

/** The result's raw details, fetched with the full report only when opened. */
function RawDetails({ runId, index }: { runId: string; index: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      <Button
        variant="ghost"
        size="sm"
        className="w-fit gap-1 px-0 text-muted-foreground"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden />
        {open ? "Hide raw details" : "Show raw details"}
      </Button>
      {open && <RawDetailsBody runId={runId} index={index} />}
    </div>
  );
}

function RawDetailsBody({ runId, index }: { runId: string; index: number }) {
  const { data, isPending, error } = useRun(runId);
  if (isPending) return <Skeleton className="h-24 w-full" />;
  if (error) {
    return (
      <Notice title="Could not load details" tone="error">
        {error.message}
      </Notice>
    );
  }
  return (
    <pre className="max-h-80 overflow-auto rounded-md border border-border bg-muted/60 p-3 font-mono text-xs">
      {JSON.stringify(data.results[index]?.details ?? {}, null, 2)}
    </pre>
  );
}
