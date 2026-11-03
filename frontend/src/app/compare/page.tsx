import { Suspense } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { Skeleton } from "@/components/ui/skeleton";

import { CompareView } from "./compare-view";

export default function ComparePage() {
  return (
    <div className="flex max-w-6xl flex-col gap-8">
      <PageHeader
        eyebrow="History"
        title="Compare runs"
        description="Pick any two finished runs - or two taxonomies scored in one run - to see every metric side by side, how much it changed, and whether that change is an improvement."
      />
      {/* The picked runs are read from the URL, which needs a Suspense boundary to prerender. */}
      <Suspense fallback={<Skeleton className="h-40 w-full" />}>
        <CompareView />
      </Suspense>
    </div>
  );
}
