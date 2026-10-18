"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { TaxonomySource } from "@/lib/api/client";
import { useTaxonomyConcept } from "@/lib/api/queries";
import { conceptLevelSchema, type TaxonomyConceptRef, type TaxonomyPath } from "@/lib/api/schemas";

function sourceLink(source: TaxonomySource, level: string, conceptId: string | number): string {
  const params = new URLSearchParams({ run_id: source.runId, dataset: source.dataset });
  return `/taxonomy/${level}/${encodeURIComponent(String(conceptId))}?${params}`;
}

/** A path's steps as breadcrumb links, in order, skipping any that are null
 * (an orphan row - e.g. a speciality whose category doesn't resolve - still
 * shows whatever of the path does exist, rather than hiding the row). */
function PathBreadcrumb({ path, source }: { path: TaxonomyPath; source: TaxonomySource }) {
  const steps: { level: string; ref: TaxonomyConceptRef }[] = [
    { level: "services", ref: path.service! },
    { level: "categories", ref: path.category! },
    { level: "specialities", ref: path.speciality! },
  ].filter((step): step is { level: string; ref: TaxonomyConceptRef } => step.ref !== null);

  if (steps.length === 0) return <span className="text-sm text-muted-foreground">No resolvable parent.</span>;
  return (
    <div className="flex flex-wrap items-center gap-1 text-sm">
      {steps.map((step, index) => (
        <span key={step.level} className="flex items-center gap-1">
          {index > 0 && <span className="text-muted-foreground">/</span>}
          <Link href={sourceLink(source, step.level, step.ref.id)} className="hover:underline">
            {step.ref.name}
          </Link>
        </span>
      ))}
    </div>
  );
}

function ConceptRefList({ refs, level, source }: { refs: TaxonomyConceptRef[]; level: string; source: TaxonomySource }) {
  if (refs.length === 0) return <p className="text-sm text-muted-foreground">None.</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {refs.map((ref) => (
        <Link key={ref.id} href={sourceLink(source, level, ref.id)}>
          <Badge variant="outline">{ref.name}</Badge>
        </Link>
      ))}
    </div>
  );
}

const CHILD_LEVEL: Record<string, string | null> = {
  services: "categories",
  categories: "specialities",
  specialities: null,
  tags: null,
};

export function ConceptDetail({
  level,
  conceptId,
  runId,
  dataset,
}: {
  level: string;
  conceptId: string;
  runId: string | undefined;
  dataset: string | undefined;
}) {
  const parsedLevel = conceptLevelSchema.safeParse(level);
  const source: TaxonomySource | null = runId && dataset ? { runId, dataset } : null;
  const { data: concept, isPending, error } = useTaxonomyConcept(
    source,
    parsedLevel.success ? parsedLevel.data : "tags",
    conceptId,
  );

  if (!parsedLevel.success) {
    return <p className="text-sm text-destructive">Unknown concept level: {level}</p>;
  }
  if (!source) {
    return (
      <p className="text-sm text-destructive">
        Missing run_id/dataset - open this page via search results, not directly.
      </p>
    );
  }
  if (isPending) return <Skeleton className="h-64 w-full max-w-5xl" />;
  if (error) return <p className="text-sm text-destructive">Could not load concept: {error.message}</p>;

  const childLevel = CHILD_LEVEL[concept.level];

  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <header className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold">{concept.name}</h1>
        <Badge variant="outline">{concept.level}</Badge>
      </header>
      <Link href="/taxonomy" className="text-sm text-muted-foreground hover:underline">
        &larr; Back to search
      </Link>

      <Card>
        <CardHeader>
          <CardTitle>{concept.paths.length > 1 ? "Paths" : "Path"}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {concept.paths.length === 0 && <p className="text-sm text-muted-foreground">No linked parent.</p>}
          {concept.paths.map((path, index) => (
            <PathBreadcrumb key={index} path={path} source={source} />
          ))}
        </CardContent>
      </Card>

      {childLevel && (
        <Card>
          <CardHeader>
            <CardTitle>{childLevel}</CardTitle>
          </CardHeader>
          <CardContent>
            <ConceptRefList refs={concept.children} level={childLevel} source={source} />
          </CardContent>
        </Card>
      )}

      {concept.level === "specialities" && (
        <Card>
          <CardHeader>
            <CardTitle>Tags</CardTitle>
          </CardHeader>
          <CardContent>
            <ConceptRefList refs={concept.tags} level="tags" source={source} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
