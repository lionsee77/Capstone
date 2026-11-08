"use client";

import Link from "next/link";

import { Notice } from "@/components/layout/page-header";
import { StatusDot } from "@/components/run-status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useRounds } from "@/lib/api/queries";
import { formatDateTime } from "@/lib/format";
import { roundStatusLabel } from "@/lib/rounds";

export function RoundsTable() {
  const { data, isPending, error } = useRounds();

  if (isPending) return <Skeleton className="h-40 w-full" />;
  if (!data) {
    return (
      <Notice title="Could not load rounds" tone="error">
        {error?.message}
      </Notice>
    );
  }
  if (data.length === 0) {
    return (
      <Notice title="No rounds yet">
        Start one from <Link href="/sources/new" className="underline underline-offset-4">New round</Link>. Rounds run
        from the command line appear on the Improvement page.
      </Notice>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Round</TableHead>
          <TableHead>Source</TableHead>
          <TableHead>Built on</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Created</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map((round) => (
          <TableRow key={round.round_id}>
            <TableCell>
              <Link
                href={`/rounds/${encodeURIComponent(round.round_id)}`}
                className="underline-offset-4 hover:text-primary hover:underline"
              >
                {round.round_id}
              </Link>
            </TableCell>
            <TableCell>{round.source_name ?? "Not set"}</TableCell>
            <TableCell>{round.baseline_snapshot}</TableCell>
            <TableCell>
              <StatusDot status={round.status === "running" || round.status === "failed" ? round.status : "pending"} />
              <span className="ml-2 text-sm text-muted-foreground">{roundStatusLabel(round.status)}</span>
            </TableCell>
            <TableCell>{formatDateTime(round.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
