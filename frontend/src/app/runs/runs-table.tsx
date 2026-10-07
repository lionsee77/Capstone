"use client";

import Link from "next/link";
import { useState } from "react";

import { RunStatusBadge } from "@/components/run-status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { hasOlderRuns, RUNS_PAGE_SIZE, useRuns } from "@/lib/api/queries";

export function RunsTable() {
  const [limit, setLimit] = useState(RUNS_PAGE_SIZE);
  // isPlaceholderData: still showing the previous page while a larger one loads (not background polls).
  const { data, isPending, isPlaceholderData, error, refetch } = useRuns(limit);

  if (isPending) return <Skeleton className="h-40 w-full" />;
  if (error) {
    return (
      <div className="flex flex-col items-start gap-2">
        <p className="text-sm text-destructive">Could not load runs: {error.message}</p>
        <Button variant="outline" onClick={() => refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  if (data.length === 0) return <p className="text-sm text-muted-foreground">No runs yet.</p>;

  const mayHaveMore = hasOlderRuns(data.length, limit, isPlaceholderData);

  return (
    <div className="flex flex-col gap-4">
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
                <Link href={`/runs/${run.run_id}`} className="hover:underline">
                  {run.run_id.slice(0, 8)}
                </Link>
              </TableCell>
              <TableCell>
                <RunStatusBadge status={run.status} />
              </TableCell>
              <TableCell>{new Date(run.created_at).toLocaleString()}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {mayHaveMore && (
        <Button
          variant="outline"
          className="self-center"
          disabled={isPlaceholderData}
          onClick={() => setLimit((current) => current + RUNS_PAGE_SIZE)}
        >
          {isPlaceholderData ? "Loading…" : "Load older runs"}
        </Button>
      )}
    </div>
  );
}
