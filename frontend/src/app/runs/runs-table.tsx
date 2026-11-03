"use client";

import Link from "next/link";
import { useState } from "react";

import { Notice } from "@/components/layout/page-header";
import { RunStatusBadge } from "@/components/run-status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { hasOlderRuns, RUNS_PAGE_SIZE, useLoadOlderRuns, useRuns } from "@/lib/api/queries";
import { formatDateTime, shortRunId } from "@/lib/format";

export function RunsTable() {
  const [limit, setLimit] = useState(RUNS_PAGE_SIZE);
  const { data, isPending, error, refetch } = useRuns(limit);
  const loadOlder = useLoadOlderRuns(setLimit);

  if (isPending) return <Skeleton className="h-40 w-full" />;
  // Only a first load with nothing to show replaces the table; a failed poll keeps the cached rows.
  if (!data) {
    return (
      <Notice title="Could not load runs" tone="error">
        <p>{error?.message}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => refetch()}>
          Try again
        </Button>
      </Notice>
    );
  }
  if (data.length === 0) {
    return (
      <Notice title="No runs yet">
        Score a config with <code className="font-mono text-foreground">taxonomy-pipeline run</code> and it appears here.
      </Notice>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {error && (
        <p className="text-sm text-destructive">Could not refresh runs ({error.message}); showing the last loaded list.</p>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Run</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Created</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((run) => (
            <TableRow key={run.run_id}>
              <TableCell className="font-mono">
                <Link href={`/runs/${run.run_id}`} className="text-primary underline-offset-4 hover:underline">
                  {shortRunId(run.run_id)}
                </Link>
              </TableCell>
              <TableCell>
                <RunStatusBadge status={run.status} />
              </TableCell>
              <TableCell className="text-muted-foreground">{formatDateTime(run.created_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {hasOlderRuns(data.length, limit) && (
        <div className="flex flex-col items-center gap-2">
          <Button
            variant="outline"
            disabled={loadOlder.isPending}
            onClick={() => loadOlder.mutate(limit + RUNS_PAGE_SIZE)}
          >
            {loadOlder.isPending ? "Loading…" : "Load older runs"}
          </Button>
          {loadOlder.error && (
            <p className="text-sm text-destructive">Could not load older runs: {loadOlder.error.message}</p>
          )}
        </div>
      )}
    </div>
  );
}
