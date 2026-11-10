import { cn } from "@/lib/utils";

/** A plain select, for long lists of data such as columns or placements, where the browser's own list works best. */
export function NativeSelect({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      className={cn("h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm disabled:opacity-50", className)}
      {...props}
    />
  );
}
