import { Badge } from "@/components/ui/badge";
import type { RunStatus } from "@/lib/api/schemas";

const VARIANTS = {
  queued: "outline",
  running: "secondary",
  done: "default",
  failed: "destructive",
} as const satisfies Record<RunStatus, string>;

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return <Badge variant={VARIANTS[status]}>{status}</Badge>;
}
