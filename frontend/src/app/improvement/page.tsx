import { ImprovementView } from "./improvement-view";

export default function ImprovementPage() {
  return (
    <div className="flex max-w-6xl flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">How the taxonomy improved</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Each enrichment round adds a new source&apos;s tags to the taxonomy. This page follows the rounds from the
          first baseline to the latest and compares every quality metric between them, all scored in one run so each
          change is like for like.
        </p>
      </header>
      <ImprovementView />
    </div>
  );
}
