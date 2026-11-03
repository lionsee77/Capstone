"use client";

import { useMetricCatalogue } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

/** Whether the pipeline API answered, read from the metric catalogue query the
 * overview already makes (cached forever), so it costs no extra request. */
export function ApiStatus() {
  const { isPending, isError } = useMetricCatalogue();
  const state = isPending ? "connecting" : isError ? "unreachable" : "connected";
  return (
    <p className="flex items-center gap-2 text-xs text-muted-foreground">
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          state === "connected" && "bg-success",
          state === "unreachable" && "bg-destructive",
          state === "connecting" && "animate-pulse bg-muted-foreground",
        )}
      />
      Pipeline API {state}
    </p>
  );
}
