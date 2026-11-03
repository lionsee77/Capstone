import { Suspense } from "react";

import { Skeleton } from "@/components/ui/skeleton";

import { CompareView } from "./compare-view";

export default function ComparePage() {
  return (
    <div className="flex max-w-6xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">Compare runs</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Pick any two finished runs - or two taxonomies scored in one run - to see every metric side by side, how
          much it changed, and whether that change is an improvement.
        </p>
      </header>
      {/* The picked runs are read from the URL, which needs a Suspense boundary to prerender. */}
      <Suspense fallback={<Skeleton className="h-40 w-full" />}>
        <CompareView />
      </Suspense>
    </div>
  );
}
