export default function NewSourcePage() {
  return (
    <div className="flex max-w-5xl flex-col gap-2">
      <h1 className="text-2xl font-semibold">New data source</h1>
      {/* TODO: form backed by `useSourceDraftStore` (datasets + metrics), validated with
          `pipelineConfigSchema`, submitted with `useCreateRun`, then redirect to /runs/[runId]. */}
      <p className="text-sm text-muted-foreground">
        Register a dataset and validate it against the existing taxonomy - coming next.
      </p>
    </div>
  );
}
