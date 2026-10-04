"use client";

import Link from "next/link";

import { RunStatusBadge } from "@/components/run-status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useRuns } from "@/lib/api/queries";

export function RunsTable() {
  const { data, isPending, error } = useRuns();

  if (isPending) return <Skeleton className="h-40 w-full" />;
  if (error) return <p className="text-sm text-destructive">Could not load runs: {error.message}</p>;
  if (data.length === 0) return <p className="text-sm text-muted-foreground">No runs yet.</p>;

  return (
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
  );
}
