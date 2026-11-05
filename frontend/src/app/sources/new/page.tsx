import { Notice, PageHeader } from "@/components/layout/page-header";

export default function NewSourcePage() {
  return (
    <div className="flex max-w-5xl flex-col gap-8">
      <PageHeader
        eyebrow="Sources"
        title="New data source"
        description="Register a dataset and validate it against the existing taxonomy."
      />
      {/* TODO: form backed by `useSourceDraftStore` (datasets + metrics), validated with
          `pipelineConfigSchema`, submitted with `useCreateRun`, then redirect to /runs/[runId]. */}
      <Notice title="Not available in the dashboard yet">
        Import a source from the command line with{" "}
        <code className="font-mono text-foreground">taxonomy-pipeline import-source</code>, then score it with{" "}
        <code className="font-mono text-foreground">taxonomy-pipeline run</code>.
      </Notice>
    </div>
  );
}
