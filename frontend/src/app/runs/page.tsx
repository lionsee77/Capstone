import { RunsTable } from "./runs-table";

export default function RunsPage() {
  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold">Validation runs</h1>
        <p className="text-sm text-muted-foreground">Run history from the pipeline&apos;s SQLite store.</p>
      </header>
      <RunsTable />
    </div>
  );
}
