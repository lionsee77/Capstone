"use client";

import { useApiHealth } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

/** Whether the pipeline API answers, re-checked periodically (`useApiHealth`). A failed
 * check shows as unreachable even when an earlier one succeeded. */
export function ApiStatus() {
  const { isPending, isError } = useApiHealth();
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
