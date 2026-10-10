"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { TaxonomySource } from "@/lib/api/client";
import { useRun, useRuns, useTaxonomySearch } from "@/lib/api/queries";
import type { ConceptLevel } from "@/lib/api/schemas";

const LEVELS: ConceptLevel[] = ["services", "categories", "specialities", "tags"];
const ALL_LEVELS = "all" as const;

/** Picks a finished run and a dataset within it - the (run_id, dataset) pair
 * every /taxonomy endpoint needs, since nothing keeps a dataset loaded
 * between requests. */
function useSourcePicker() {
  const { data: runs } = useRuns();
  const doneRuns = useMemo(() => (runs ?? []).filter((run) => run.status === "done"), [runs]);
  const [runId, setRunId] = useState<string>("");
  const { data: run } = useRun(runId);
  const [dataset, setDataset] = useState<string>("");

  function selectRun(id: string | null) {
    setRunId(id ?? "");
    setDataset(""); // the previous dataset name may not exist in the newly picked run
  }

  const source: TaxonomySource | null = runId && dataset ? { runId, dataset } : null;
  return { doneRuns, runId, selectRun, run, dataset, setDataset, source };
}

export function TaxonomySearch() {
  const { doneRuns, runId, selectRun, run, dataset, setDataset, source } = useSourcePicker();
  const [q, setQ] = useState("");
  const [level, setLevel] = useState<ConceptLevel | "">("");
  const { data: results, isPending, error } = useTaxonomySearch(source, q, level);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="taxonomy-run">Run</Label>
          <Select value={runId} onValueChange={selectRun}>
            <SelectTrigger id="taxonomy-run" className="w-56">
              <SelectValue placeholder="Pick a finished run" />
            </SelectTrigger>
            <SelectContent>
              {doneRuns.map((r) => (
                <SelectItem key={r.run_id} value={r.run_id}>
                  {r.run_id.slice(0, 8)} - {new Date(r.created_at).toLocaleString()}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="taxonomy-dataset">Dataset</Label>
          <Select value={dataset} onValueChange={(value) => setDataset(value ?? "")} disabled={!run}>
            <SelectTrigger id="taxonomy-dataset" className="w-48">
              <SelectValue placeholder="Pick a dataset" />
            </SelectTrigger>
            <SelectContent>
              {(run?.datasets ?? []).map((d) => (
                <SelectItem key={d.name} value={d.name}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="taxonomy-q">Name</Label>
          <Input
            id="taxonomy-q"
            className="w-56"
            placeholder="Search by name..."
            value={q}
            onChange={(e) => setQ(e.target.value)}
            disabled={!source}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="taxonomy-level">Level</Label>
          <Select
            value={level || ALL_LEVELS}
            onValueChange={(value) => setLevel(!value || value === ALL_LEVELS ? "" : (value as ConceptLevel))}
            disabled={!source}
          >
            <SelectTrigger id="taxonomy-level" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_LEVELS}>All levels</SelectItem>
              {LEVELS.map((l) => (
                <SelectItem key={l} value={l}>
                  {l}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {!source && <p className="text-sm text-muted-foreground">Pick a run and a dataset to search its taxonomy.</p>}
      {source && isPending && <Skeleton className="h-40 w-full" />}
      {source && error && <p className="text-sm text-destructive">Could not search: {error.message}</p>}
      {source && results && (
        <SearchResults results={results} source={source} />
      )}
    </div>
  );
}

function SearchResults({
  results,
  source,
}: {
  results: { id: string | number; name: string; level: ConceptLevel }[];
  source: TaxonomySource;
}) {
  if (results.length === 0) return <p className="text-sm text-muted-foreground">No matching concepts.</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Level</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {results.map((concept) => (
          <TableRow key={`${concept.level}-${concept.id}`}>
            <TableCell>
              <Link
                href={`/taxonomy/${concept.level}/${encodeURIComponent(String(concept.id))}?run_id=${encodeURIComponent(source.runId)}&dataset=${encodeURIComponent(source.dataset)}`}
                className="hover:underline"
              >
                {concept.name}
              </Link>
            </TableCell>
            <TableCell>
              <Badge variant="outline">{concept.level}</Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
