"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Notice } from "@/components/layout/page-header";
import { LevelTag } from "@/components/level-tag";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { TaxonomySource } from "@/lib/api/client";
import { hasOlderRuns, RUNS_PAGE_SIZE, useLoadOlderRuns, useRuns, useRunSummary, useTaxonomySearch } from "@/lib/api/queries";
import { type ConceptLevel, isTaxonomyDataset } from "@/lib/api/schemas";
import { formatDateTime, shortRunId } from "@/lib/format";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const LEVELS: ConceptLevel[] = ["services", "categories", "specialities", "tags"];
const ALL_LEVELS = "all" as const;
const SEARCH_DEBOUNCE_MS = 300;

/** Picks a finished run and a dataset within it - the (run_id, dataset) pair
 * every /taxonomy endpoint needs, since nothing keeps a dataset loaded
 * between requests. */
function useSourcePicker() {
  const [limit, setLimit] = useState(RUNS_PAGE_SIZE);
  const { data: runs } = useRuns(limit);
  const loadOlder = useLoadOlderRuns(setLimit);
  const doneRuns = useMemo(() => (runs ?? []).filter((run) => run.status === "done"), [runs]);
  const [runId, setRunId] = useState<string>("");
  // Only the dataset list is needed, so skip result details (megabytes for a saturation run).
  const { data: run } = useRunSummary(runId);
  const taxonomyDatasets = useMemo(() => (run?.datasets ?? []).filter(isTaxonomyDataset), [run]);
  const [dataset, setDataset] = useState<string>("");

  function selectRun(id: string | null) {
    setRunId(id ?? "");
    setDataset(""); // the previous dataset name may not exist in the newly picked run
  }

  const source: TaxonomySource | null = runId && dataset ? { runId, dataset } : null;
  return {
    doneRuns,
    hasOlderRuns: hasOlderRuns(runs?.length ?? 0, limit),
    loadOlder: () => loadOlder.mutate(limit + RUNS_PAGE_SIZE),
    loadingOlder: loadOlder.isPending,
    runId,
    selectRun,
    taxonomyDatasets,
    dataset,
    setDataset,
    source,
  };
}

export function TaxonomySearch() {
  const {
    doneRuns, hasOlderRuns: canLoadOlder, loadOlder, loadingOlder,
    runId, selectRun, taxonomyDatasets, dataset, setDataset, source,
  } = useSourcePicker();
  const [qInput, setQInput] = useState("");
  const q = useDebouncedValue(qInput, SEARCH_DEBOUNCE_MS);
  const [level, setLevel] = useState<ConceptLevel | "">("");
  const { data: results, isPending, error } = useTaxonomySearch(source, q, level);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-4 rounded-md border border-border bg-muted/50 p-4">
        <div className="flex flex-col gap-1.5">
          <Label className="label-caps" htmlFor="taxonomy-run">Run</Label>
          <Select value={runId} onValueChange={selectRun}>
            <SelectTrigger id="taxonomy-run" className="w-56">
              <SelectValue placeholder="Pick a finished run" />
            </SelectTrigger>
            <SelectContent>
              {doneRuns.map((r) => (
                <SelectItem key={r.run_id} value={r.run_id}>
                  {shortRunId(r.run_id)} · {formatDateTime(r.created_at)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* Outside the listbox, not a SelectItem - the dropdown only offers
           * the newest page of runs, older ones need a page fetched first. */}
          {canLoadOlder && (
            <Button variant="ghost" size="sm" className="h-auto px-0 justify-start" disabled={loadingOlder} onClick={loadOlder}>
              {loadingOlder ? "Loading…" : "Load older runs"}
            </Button>
          )}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="label-caps" htmlFor="taxonomy-dataset">Dataset</Label>
          <Select value={dataset} onValueChange={(value) => setDataset(value ?? "")} disabled={!runId}>
            <SelectTrigger id="taxonomy-dataset" className="w-48">
              <SelectValue placeholder="Pick a dataset" />
            </SelectTrigger>
            <SelectContent>
              {taxonomyDatasets.map((d) => (
                <SelectItem key={d.name} value={d.name}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="label-caps" htmlFor="taxonomy-q">Name</Label>
          <Input
            id="taxonomy-q"
            className="w-56"
            placeholder="Search by name…"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            disabled={!source}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="label-caps" htmlFor="taxonomy-level">Level</Label>
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

      {!source && (
        <Notice title="Choose a run and a dataset">
          The taxonomy is read from a finished validation run, so pick one above to start searching.
        </Notice>
      )}
      {source && isPending && <Skeleton className="h-40 w-full" />}
      {source && error && (
        <Notice title="Could not search" tone="error">
          {error.message}
        </Notice>
      )}
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
  if (results.length === 0) return <Notice title="No matching concepts">Try a shorter name or another level.</Notice>;
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
                className="underline-offset-4 hover:text-primary hover:underline"
              >
                {concept.name}
              </Link>
            </TableCell>
            <TableCell>
              <LevelTag level={concept.level} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
