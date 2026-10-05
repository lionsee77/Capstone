export default function TaxonomyPage() {
  return (
    <div className="flex max-w-5xl flex-col gap-2">
      <h1 className="text-2xl font-semibold">Taxonomy</h1>
      {/* TODO: needs a backend endpoint that serves the loaded taxonomy tree
          (services -> categories -> specialities -> tags); none exists yet. */}
      <p className="text-sm text-muted-foreground">
        Taxonomy tree view - pending a backend endpoint that exposes a loaded dataset.
      </p>
    </div>
  );
}
