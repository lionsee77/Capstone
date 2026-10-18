import { TaxonomySearch } from "./taxonomy-search";

export default function TaxonomyPage() {
  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold">Taxonomy</h1>
        <p className="text-sm text-muted-foreground">
          Search concepts from a finished validation run, and inspect how they sit in the hierarchy.
        </p>
      </header>
      <TaxonomySearch />
    </div>
  );
}
