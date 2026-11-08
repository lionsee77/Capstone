"use client";

import { useState } from "react";

import { Notice, Section } from "@/components/layout/page-header";
import { LevelTag } from "@/components/level-tag";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useTagLookup } from "@/lib/api/queries";
import type { ConceptLevel, LookupMatch } from "@/lib/api/schemas";
import { isLookupTerm, lookupVerdict, matchLabel, MAX_TERM_LENGTH, parentLabel } from "@/lib/lookup";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const LEVELS: ConceptLevel[] = ["services", "categories", "specialities", "tags"];
const ALL_LEVELS = "all" as const;
const DEBOUNCE_MS = 300;

export function TagLookup() {
  const [input, setInput] = useState("");
  const term = useDebouncedValue(input.trim(), DEBOUNCE_MS);
  const [level, setLevel] = useState<ConceptLevel | "">("");
  const [semantic, setSemantic] = useState(false);
  const { data: result, isPending, error } = useTagLookup(term, level, semantic);
  const searching = isLookupTerm(term);
  const verdict = result ? lookupVerdict(result) : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-4 rounded-md border border-border bg-muted/50 p-4">
        <div className="flex min-w-56 flex-1 flex-col gap-1.5">
          <Label className="label-caps" htmlFor="lookup-term">Term</Label>
          <Input
            id="lookup-term"
            placeholder="e.g. Tax Advisory"
            maxLength={MAX_TERM_LENGTH}
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="label-caps" htmlFor="lookup-level">Level</Label>
          <Select
            value={level || ALL_LEVELS}
            onValueChange={(value) => setLevel(!value || value === ALL_LEVELS ? "" : (value as ConceptLevel))}
          >
            <SelectTrigger id="lookup-level" className="w-40">
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
        <label className="flex h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={semantic} onChange={(e) => setSemantic(e.target.checked)} />
          Also match by meaning
        </label>
      </div>

      {semantic && (
        <p className="text-sm text-muted-foreground">
          The first search by meaning loads a language model, which can take a few seconds. Treat these matches as
          suggestions: similar words can still mean different things.
        </p>
      )}

      {!searching && (
        <Notice title="Enter a term to check">
          Results show exact matches, including spelling variants, and names that are close to it.
        </Notice>
      )}
      {searching && isPending && <Skeleton className="h-40 w-full" />}
      {searching && error && (
        <Notice title="Could not check this term" tone="error">
          {error.message}
        </Notice>
      )}
      {searching && result && verdict && (
        <div className="flex flex-col gap-6" data-verdict={verdict.kind}>
          <Notice title={verdict.title}>{verdict.detail}</Notice>
          {result.exact.length > 0 && <MatchTable title="Exact matches" matches={result.exact} />}
          {result.near.length > 0 && <MatchTable title="Close matches" matches={result.near} />}
        </div>
      )}
    </div>
  );
}

function MatchTable({ title, matches }: { title: string; matches: LookupMatch[] }) {
  return (
    <Section title={title}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Level</TableHead>
            <TableHead>Match</TableHead>
            <TableHead>Sits under</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {matches.map((match) => (
            <TableRow key={`${match.level}-${match.id}`}>
              <TableCell className="whitespace-normal">
                {match.name}
                {!match.active && <span className="ml-2 text-xs text-muted-foreground">(retired)</span>}
              </TableCell>
              <TableCell>
                <LevelTag level={match.level} />
              </TableCell>
              <TableCell className="whitespace-normal">{matchLabel(match)}</TableCell>
              <TableCell className="min-w-48 whitespace-normal">
                <Parents match={match} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Section>
  );
}

/** One line per parent chain: a tag can sit under several specialities, and a service under nothing. */
function Parents({ match }: { match: LookupMatch }) {
  const lines = match.paths.map((path) => parentLabel(path, match.level)).filter(Boolean);
  if (lines.length === 0) {
    return (
      <span className="text-muted-foreground">
        {match.level === "services" ? "Top level" : "Not linked to a parent"}
      </span>
    );
  }
  return (
    <ul className="flex flex-col gap-1">
      {lines.map((line, index) => (
        <li key={index}>{line}</li>
      ))}
    </ul>
  );
}
