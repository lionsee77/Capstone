import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";

import { RoundsTable } from "./rounds-table";

export default function RoundsPage() {
  return (
    <div className="flex max-w-5xl flex-col gap-8">
      <PageHeader
        eyebrow="Enrichment"
        title="Enrichment rounds"
        description="Each round brings one new source into the taxonomy, with a person reviewing what's uncertain."
        actions={
          <Link href="/sources/new" className="text-sm text-primary underline-offset-4 hover:underline">
            New round
          </Link>
        }
      />
      <RoundsTable />
    </div>
  );
}
