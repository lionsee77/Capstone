"use client";

import Link from "next/link";

import { Notice, PageHeader, Section } from "@/components/layout/page-header";
import { LevelTag } from "@/components/level-tag";
import { Skeleton } from "@/components/ui/skeleton";
import type { TaxonomySource } from "@/lib/api/client";
import { useTaxonomyConcept } from "@/lib/api/queries";
import type { ConceptLevel, TaxonomyConceptRef, TaxonomyPath } from "@/lib/api/schemas";

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
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 py-2 text-sm">
      {steps.map((step, index) => (
        <li key={step.level} className="flex items-center gap-2">
          {index > 0 && (
            <span aria-hidden className="text-muted-foreground">
              ›
            </span>
          )}
          <Link
            href={sourceLink(source, step.level, step.ref.id)}
            className="underline-offset-4 hover:text-primary hover:underline"
          >
            {step.ref.name}
          </Link>
        </li>
      ))}
    </ol>
  );
}

function ConceptRefList({ refs, level, source }: { refs: TaxonomyConceptRef[]; level: string; source: TaxonomySource }) {
  if (refs.length === 0) return <p className="text-sm text-muted-foreground">None.</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {refs.map((ref) => (
        <Link
          key={ref.id}
          href={sourceLink(source, level, ref.id)}
          className="rounded-sm border border-rule px-2 py-1 text-sm transition-colors hover:border-primary hover:bg-accent hover:text-accent-foreground"
        >
          {ref.name}
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
  level: ConceptLevel;
  conceptId: string;
  runId: string | undefined;
  dataset: string | undefined;
}) {
  const source: TaxonomySource | null = runId && dataset ? { runId, dataset } : null;
  const { data: concept, isPending, error } = useTaxonomyConcept(source, level, conceptId);

  if (!source) {
    return (
      <Notice title="Missing run or dataset" tone="error">
        Open this page from the taxonomy search results, not directly.
      </Notice>
    );
  }
  if (isPending) return <Skeleton className="h-64 w-full max-w-5xl" />;
  if (error) {
    return (
      <Notice title="Could not load concept" tone="error">
        {error.message}
      </Notice>
    );
  }

  const childLevel = CHILD_LEVEL[concept.level];

  return (
    <div className="flex max-w-5xl flex-col gap-10">
      <div className="flex flex-col gap-4">
        <Link href="/taxonomy" className="text-sm text-muted-foreground hover:text-foreground">
          &larr; Back to search
        </Link>
        <PageHeader
          eyebrow={`${source.dataset} · ${concept.level}`}
          title={concept.name}
          actions={<LevelTag level={concept.level} />}
        />
      </div>

      <Section title={concept.paths.length > 1 ? "Paths" : "Path"}>
        {concept.paths.length === 0 && <p className="text-sm text-muted-foreground">No linked parent.</p>}
        <div className="divide-y divide-border">
          {concept.paths.map((path, index) => (
            <PathBreadcrumb key={index} path={path} source={source} />
          ))}
        </div>
      </Section>

      {childLevel && (
        <Section title={<span className="capitalize">{childLevel}</span>} aside={<span className="label-caps">{concept.children.length}</span>}>
          <ConceptRefList refs={concept.children} level={childLevel} source={source} />
        </Section>
      )}

      {concept.level === "specialities" && (
        <Section title="Tags" aside={<span className="label-caps">{concept.tags.length}</span>}>
          <ConceptRefList refs={concept.tags} level="tags" source={source} />
        </Section>
      )}
    </div>
  );
}
