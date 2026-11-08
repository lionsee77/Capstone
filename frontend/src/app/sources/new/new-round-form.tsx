"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { Notice, Section } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useConfigureRound,
  useCreateRound,
  useRemoveRoundFile,
  useRound,
  useRoundFile,
  useRoundFiles,
  useRoundOptions,
  useRunRound,
  useUploadRoundFile,
} from "@/lib/api/queries";
import type { RoundFile, RoundOptions, RoundState } from "@/lib/api/schemas";
import {
  buildMapping,
  buildSettings,
  draftFromMapping,
  draftKey,
  draftFromSettings,
  droppedFiles,
  type FilterDraft,
  type LevelDraft,
  mappingProblems,
  type MappingDraft,
  roundIdProblem,
  type SettingsDraft,
  suggestSourceName,
  uploadProblem,
} from "@/lib/round-setup";

export function NewRoundForm({ roundId }: { roundId: string | null }) {
  const { data: options, error } = useRoundOptions();
  if (error) {
    return (
      <Notice title="Could not reach the pipeline" tone="error">
        {error.message}
      </Notice>
    );
  }
  if (!options) return <Skeleton className="h-40 w-full" />;
  return roundId ? <SetupRound roundId={roundId} options={options} /> : <StartRound options={options} />;
}

function StartRound({ options }: { options: RoundOptions }) {
  const router = useRouter();
  const create = useCreateRound();
  const [roundId, setRoundId] = useState("");
  const [baseline, setBaseline] = useState(options.snapshots[0] ?? "");
  const problem = roundId ? roundIdProblem(roundId) : null;

  if (options.snapshots.length === 0) {
    return (
      <Notice title="There is no taxonomy snapshot to build on yet">
        A round adds to an integrated snapshot. Create the first one with the command line: see the pipeline README.
      </Notice>
    );
  }
  return (
    <Section title="1. Name the round">
      <div className="flex flex-wrap items-start gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="round-id">Round name</Label>
          <Input id="round-id" className="w-64" placeholder="e.g. onet-demo" value={roundId} onChange={(e) => setRoundId(e.target.value)} />
          {problem && <p className="max-w-64 text-sm text-destructive">{problem}</p>}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="round-baseline">Build on</Label>
          <NativeSelect id="round-baseline" className="w-64" value={baseline} onChange={(e) => setBaseline(e.target.value)}>
            {options.snapshots.map((snapshot) => (
              <option key={snapshot} value={snapshot}>{snapshot}</option>
            ))}
          </NativeSelect>
          <p className="max-w-64 text-sm text-muted-foreground">The newest is first. New tags are checked against this version.</p>
        </div>
      </div>
      <div>
        <Button
          disabled={!roundId || problem !== null || create.isPending}
          onClick={() => create.mutate(
            { round_id: roundId, baseline_snapshot: baseline },
            { onSuccess: (round) => router.replace(`/sources/new?round=${encodeURIComponent(round.round_id)}`) },
          )}
        >
          Create round
        </Button>
      </div>
      {create.error && <p className="text-sm text-destructive">{create.error.message}</p>}
    </Section>
  );
}

function SetupRound({ roundId, options }: { roundId: string; options: RoundOptions }) {
  const { data: round, error } = useRound(roundId);
  const { data: files = [] } = useRoundFiles(roundId);
  if (error) {
    return (
      <Notice title="Could not load this round" tone="error">
        {error.message}
      </Notice>
    );
  }
  if (!round) return <Skeleton className="h-40 w-full" />;
  return (
    <div className="flex flex-col gap-8">
      <p className="text-sm text-muted-foreground">Round <span className="font-medium text-foreground">{round.round_id}</span>, built on {round.baseline_snapshot}.</p>
      <AddFiles round={round} files={files} />
      {files.length > 0 && <ConfigureRound key={round.round_id} round={round} files={files} options={options} />}
    </div>
  );
}

function AddFiles({ round, files }: { round: RoundState; files: RoundFile[] }) {
  const upload = useUploadRoundFile(round.round_id);
  const remove = useRemoveRoundFile(round.round_id);
  const [dragging, setDragging] = useState(false);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [uploading, setUploading] = useState(0);
  const folderInput = useRef<HTMLInputElement>(null);

  // `webkitdirectory` isn't in React's input props, so a folder picker has to be switched on directly.
  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);

  async function add(incoming: File[]) {
    const accepted = incoming.filter((file) => uploadProblem(file) === null);
    setSkipped(incoming.filter((file) => uploadProblem(file) !== null).map((file) => `${file.name}: ${uploadProblem(file)}`));
    setUploading(accepted.length);
    for (const file of accepted) {
      try {
        await upload.mutateAsync(file);
      } catch {
        break; // `upload.error` says why; the files before it are saved
      }
      setUploading((left) => left - 1);
    }
    setUploading(0);
  }

  return (
    <Section title="2. Add the source files">
      <div
        role="group"
        aria-label="Drop files or a folder here"
        data-testid="drop-zone"
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void droppedFiles(e.dataTransfer).then(add);
        }}
        className={`flex flex-col items-center gap-3 rounded-md border-2 border-dashed p-8 text-center ${dragging ? "border-primary bg-accent" : "border-border"}`}
      >
        <p className="text-sm">Drop a file or folder here</p>
        <p className="text-sm text-muted-foreground">One table with a row per speciality and tag, plus any code-label files. CSV, TSV or Excel, up to 50 MB each.</p>
        <div className="flex gap-2">
          <Label className="cursor-pointer rounded-lg border border-border px-2.5 py-1.5 text-sm hover:bg-muted">
            Choose files
            <input
              type="file"
              multiple
              className="sr-only"
              accept=".csv,.tsv,.xlsx,.xlsm"
              onChange={(e) => {
                void add([...(e.target.files ?? [])]);
                e.target.value = "";
              }}
            />
          </Label>
          <Label className="cursor-pointer rounded-lg border border-border px-2.5 py-1.5 text-sm hover:bg-muted">
            Choose a folder
            <input
              ref={folderInput}
              type="file"
              className="sr-only"
              onChange={(e) => {
                void add([...(e.target.files ?? [])]);
                e.target.value = "";
              }}
            />
          </Label>
        </div>
      </div>
      {uploading > 0 && <p className="text-sm text-muted-foreground">Uploading {uploading} file(s)…</p>}
      {upload.error && <p className="text-sm text-destructive">{upload.error.message}</p>}
      {skipped.length > 0 && (
        <p className="text-sm text-muted-foreground">Skipped {skipped.length}: {skipped.slice(0, 5).join("; ")}{skipped.length > 5 ? "; …" : ""}</p>
      )}
      {files.length > 0 && (
        <ul className="flex flex-col divide-y divide-rule">
          {files.map((file) => (
            <li key={file.name} className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
              <span>
                <span className="font-medium">{file.name}</span>
                <span className="ml-2 text-muted-foreground">
                  {file.error ?? `${file.row_count.toLocaleString()} rows, ${file.columns.length} columns`}
                </span>
              </span>
              <Button size="xs" variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate(file.name)}>Remove</Button>
            </li>
          ))}
        </ul>
      )}
      {remove.error && <p className="text-sm text-destructive">{remove.error.message}</p>}
    </Section>
  );
}

function ConfigureRound({ round, files, options }: { round: RoundState; files: RoundFile[]; options: RoundOptions }) {
  const router = useRouter();
  const [mapping, setMapping] = useState<MappingDraft>(() => draftFromMapping(round.mapping));
  const [settings, setSettings] = useState<SettingsDraft>(() => draftFromSettings(round.settings));
  const configure = useConfigureRound(round.round_id);
  const run = useRunRound(round.round_id);

  // A workbook with a bad first sheet can still have a good one, so it stays choosable; a CSV that can't be read can't.
  const readable = files.filter((file) => !file.error || file.sheets.length > 0);
  const source = mapping.source || "";
  const { data: chosen } = useRoundFile(round.round_id, source || null, mapping.sheet || null);
  const fallback = useMemo(() => files.find((file) => file.name === source), [files, source]);
  const table = chosen ?? fallback;
  const problems = mappingProblems(mapping);
  const update = (patch: Partial<MappingDraft>) => setMapping((current) => ({ ...current, ...patch }));
  const saved = round.status === "ready" && !configure.isPending;
  // What's on screen must be what would run: change anything after importing and it has to be imported again.
  const currentKey = draftKey(mapping, settings);
  const [importedKey, setImportedKey] = useState(() => draftKey(draftFromMapping(round.mapping), draftFromSettings(round.settings)));
  const upToDate = saved && currentKey === importedKey;

  function chooseSource(name: string) {
    update({
      source: name, sheet: "",
      name: mapping.name || suggestSourceName(name),
      displayName: mapping.displayName || name.replace(/\.[^.]+$/, ""),
    });
  }

  return (
    <>
      <Section title="3. Say how to read it">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="File to import" id="map-source">
            <NativeSelect id="map-source" value={source} onChange={(e) => chooseSource(e.target.value)}>
              <option value="">Choose a file</option>
              {readable.map((file) => <option key={file.name} value={file.name}>{file.name}</option>)}
            </NativeSelect>
          </Field>
          {(table?.sheets.length ?? 0) > 1 && (
            <Field label="Sheet" id="map-sheet">
              <NativeSelect id="map-sheet" value={mapping.sheet} onChange={(e) => update({ sheet: e.target.value })}>
                <option value="">The first sheet</option>
                {table?.sheets.map((sheet) => <option key={sheet} value={sheet}>{sheet}</option>)}
              </NativeSelect>
            </Field>
          )}
          <Field label="Source name" id="map-name" hint="Names its tables and judge cache: lowercase, digits and _.">
            <Input id="map-name" value={mapping.name} onChange={(e) => update({ name: e.target.value })} />
          </Field>
          <Field label="Display name" id="map-display" hint="How the LLM prompt names this source.">
            <Input id="map-display" value={mapping.displayName} onChange={(e) => update({ displayName: e.target.value })} />
          </Field>
        </div>

        {table?.error && (
          <Notice title="This file or sheet can't be read" tone="error">
            {table.error}
          </Notice>
        )}
        {table && !table.error && (
          <div className="flex flex-col gap-4">
            <LevelFields label="Category" hint="The broad group each row belongs to." value={mapping.category} onChange={(category) => update({ category })} table={table} files={readable} />
            <LevelFields label="Speciality" hint="The role or area under a category." value={mapping.speciality} onChange={(speciality) => update({ speciality })} table={table} files={readable} />
            <LevelFields label="Tag" hint="The skill or term itself." value={mapping.tag} onChange={(tag) => update({ tag })} table={table} files={readable} />
            <Field label="Tag description (optional)" id="map-description" hint="Extra context for classifying a tag.">
              <ColumnSelect id="map-description" table={table} value={mapping.description} onChange={(description) => update({ description })} empty="None" />
            </Field>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Categories go under" id="map-service-mode">
                <NativeSelect id="map-service-mode" value={mapping.serviceMode} onChange={(e) => update({ serviceMode: e.target.value as MappingDraft["serviceMode"] })}>
                  <option value="id">One service</option>
                  <option value="column">A service ID in each row</option>
                </NativeSelect>
              </Field>
              {mapping.serviceMode === "id" ? (
                <Field label="Service" id="map-service">
                  <NativeSelect id="map-service" value={mapping.serviceId} onChange={(e) => update({ serviceId: e.target.value })}>
                    <option value="">Choose a service</option>
                    {options.services.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}
                  </NativeSelect>
                </Field>
              ) : (
                <Field label="Service ID column" id="map-service-column">
                  <ColumnSelect id="map-service-column" table={table} value={mapping.serviceColumn} onChange={(serviceColumn) => update({ serviceColumn })} />
                </Field>
              )}
            </div>
            <Filters filters={mapping.filters} table={table} onChange={(filters) => update({ filters })} />
            <Field label="Table prefix (optional)" id="map-prefix" hint="Defaults to the source name followed by _.">
              <Input id="map-prefix" className="max-w-xs" value={mapping.tablePrefix} onChange={(e) => update({ tablePrefix: e.target.value })} />
            </Field>
          </div>
        )}
      </Section>

      <Section title="4. Set the round's options">
        <LlmSwitches settings={settings} options={options} tags={table?.row_count} onChange={setSettings} />
        <details className="rounded-md border border-border p-3">
          <summary className="cursor-pointer text-sm font-medium">Matching thresholds</summary>
          <div className="mt-3 grid gap-4 md:grid-cols-2">
            <ThresholdField id="set-threshold" label="Duplicate spelling score (0-100)" setting="threshold" value={settings.threshold} options={options} onChange={(threshold) => setSettings({ ...settings, threshold })} />
            <ThresholdField id="set-semantic" label="Duplicate meaning score (0-1)" setting="semantic_threshold" value={settings.semanticThreshold} options={options} onChange={(semanticThreshold) => setSettings({ ...settings, semanticThreshold })} />
            <ThresholdField id="set-add-lexical" label="Clearly new below spelling score (0-100)" setting="add_candidate_lexical_threshold" value={settings.addLexical} options={options} onChange={(addLexical) => setSettings({ ...settings, addLexical })} />
            <ThresholdField id="set-add-semantic" label="Clearly new below meaning score (0-1)" setting="add_candidate_semantic_threshold" value={settings.addSemantic} options={options} onChange={(addSemantic) => setSettings({ ...settings, addSemantic })} />
          </div>
        </details>
      </Section>

      <Section title="5. Import and run">
        {problems.length > 0 && (
          <ul className="flex list-disc flex-col gap-1 pl-4 text-sm text-muted-foreground">
            {problems.map((problem) => <li key={problem}>{problem}</li>)}
          </ul>
        )}
        <div>
          <Button
            variant={upToDate ? "outline" : "default"}
            disabled={problems.length > 0 || configure.isPending}
            onClick={() => configure.mutate(
              { mapping: buildMapping(mapping), settings: buildSettings(settings) },
              { onSuccess: () => setImportedKey(currentKey) },
            )}
          >
            {configure.isPending ? "Importing…" : saved ? "Import again with these settings" : "Import the source"}
          </Button>
        </div>
        {configure.error && (
          <Notice title="The source could not be imported" tone="error">
            {configure.error.message}
          </Notice>
        )}
        {saved && !upToDate && (
          <Notice title="These settings aren't imported yet">
            The last import used different settings. Import again before running, so the round runs what is shown here.
          </Notice>
        )}
        {upToDate && (
          <Notice title="Imported">
            <p>
              {round.import_counts.categories ?? 0} categories, {round.import_counts.specialities ?? 0} specialities and{" "}
              {round.import_counts.tags ?? 0} tags.
            </p>
            {round.warnings.length > 0 && (
              <ul className="mt-2 flex list-disc flex-col gap-1 pl-4">
                {round.warnings.map((warning) => <li key={warning}>{warning}</li>)}
              </ul>
            )}
          </Notice>
        )}
        {upToDate && (
          <div>
            <Button
              disabled={run.isPending}
              onClick={() => run.mutate(undefined, { onSuccess: () => router.push(`/rounds/${encodeURIComponent(round.round_id)}`) })}
            >
              Run the pipeline
            </Button>
          </div>
        )}
        {run.error && <p className="text-sm text-destructive">{run.error.message}</p>}
      </Section>
    </>
  );
}

function Field({ label, id, hint, children }: { label: string; id: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
    </div>
  );
}

function sampleText(table: RoundFile, column: string): string {
  return (table.samples[column] ?? []).slice(0, 3).join(", ");
}

function ColumnSelect({
  id, table, value, onChange, empty = "Choose a column",
}: { id: string; table: RoundFile; value: string; onChange: (value: string) => void; empty?: string }) {
  return (
    <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{empty}</option>
      {table.columns.map((column) => (
        <option key={column} value={column}>{column}{sampleText(table, column) ? ` (e.g. ${sampleText(table, column)})` : ""}</option>
      ))}
    </NativeSelect>
  );
}

function LevelFields({
  label, hint, value, onChange, table, files,
}: { label: string; hint: string; value: LevelDraft; onChange: (value: LevelDraft) => void; table: RoundFile; files: RoundFile[] }) {
  const id = label.toLowerCase();
  return (
    <div className="grid gap-3 md:grid-cols-[1fr_12rem]">
      <Field label={label} id={`map-${id}`} hint={hint}>
        <ColumnSelect id={`map-${id}`} table={table} value={value.column} onChange={(column) => onChange({ ...value, column })} />
      </Field>
      <Field label="Read it as" id={`map-${id}-mode`}>
        <NativeSelect id={`map-${id}-mode`} value={value.mode} onChange={(e) => onChange({ ...value, mode: e.target.value as LevelDraft["mode"] })}>
          <option value="column">The column as it is</option>
          <option value="prefix">A code prefix with labels</option>
        </NativeSelect>
      </Field>
      {value.mode === "prefix" && (
        <div className="grid gap-3 md:col-span-2 md:grid-cols-2">
          <Field label="Prefix length" id={`map-${id}-prefix`} hint="How many of the code's first characters name the group.">
            <Input id={`map-${id}-prefix`} type="number" min="1" value={value.prefix} onChange={(e) => onChange({ ...value, prefix: e.target.value })} />
          </Field>
          <Field label="Labels file" id={`map-${id}-labels`} hint="A CSV with code and label columns.">
            <NativeSelect id={`map-${id}-labels`} value={value.labels} onChange={(e) => onChange({ ...value, labels: e.target.value })}>
              <option value="">Choose a file</option>
              {files.filter((file) => file.name.toLowerCase().endsWith(".csv")).map((file) => (
                <option key={file.name} value={file.name}>{file.name}</option>
              ))}
            </NativeSelect>
          </Field>
        </div>
      )}
    </div>
  );
}

function Filters({ filters, table, onChange }: { filters: FilterDraft[]; table: RoundFile; onChange: (filters: FilterDraft[]) => void }) {
  const change = (index: number, patch: Partial<FilterDraft>) =>
    onChange(filters.map((filter, position) => (position === index ? { ...filter, ...patch } : filter)));
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-medium">Keep only rows where (optional)</p>
      {filters.map((filter, index) => (
        <div key={index} className="flex flex-wrap items-center gap-2">
          <div className="min-w-48 flex-1">
            <ColumnSelect id={`map-filter-${index}`} table={table} value={filter.column} onChange={(column) => change(index, { column })} />
          </div>
          <span className="text-sm text-muted-foreground">is</span>
          <Input
            aria-label={`Values for filter ${index + 1}`}
            className="max-w-56"
            placeholder={sampleText(table, filter.column) || 'Value, several with commas, "a, b" for one with a comma'}
            value={filter.values}
            onChange={(e) => change(index, { values: e.target.value })}
          />
          <Button size="xs" variant="ghost" onClick={() => onChange(filters.filter((_, position) => position !== index))}>Remove</Button>
        </div>
      ))}
      <div>
        <Button size="sm" variant="outline" onClick={() => onChange([...filters, { column: "", values: "" }])}>Add a filter</Button>
      </div>
    </div>
  );
}

function LlmSwitches({
  settings, options, tags, onChange,
}: { settings: SettingsDraft; options: RoundOptions; tags?: number; onChange: (settings: SettingsDraft) => void }) {
  const available = options.llm_allowed && options.llm_key_configured;
  return (
    <div className="flex flex-col gap-3">
      {!available && (
        <Notice title="LLM steps are off on this server">
          {options.llm_allowed
            ? "Add a real OPENAI_API_KEY to backend/.env to use them."
            : "To use them, set TAXONOMY_ALLOW_LLM=true in backend/.env and restart the API."}{" "}
          Without them the round still runs, with more tags left for you to review.
        </Notice>
      )}
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" disabled={!available} checked={settings.judge} onChange={(e) => onChange({ ...settings, judge: e.target.checked })} />
        <span>
          <span className="font-medium">Use the LLM judge for ambiguous tags (spends credits)</span>
          <span className="block text-muted-foreground">Settles most tags saturation can&apos;t call, so fewer reach review. Earlier answers for this source are reused free.</span>
        </span>
      </label>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" disabled={!available} checked={settings.caseB} onChange={(e) => onChange({ ...settings, caseB: e.target.checked })} />
        <span>
          <span className="font-medium">Classify tags without a parent with the LLM (spends credits)</span>
          <span className="block text-muted-foreground">Otherwise those tags are left out of this round, with a warning.</span>
        </span>
      </label>
      {(settings.judge || settings.caseB) && (
        <p className="text-sm text-destructive">
          This round will spend OpenAI credits{tags ? `, on at most ${tags.toLocaleString()} rows' worth of tags` : ""}. Scoring the
          finished snapshot uses the judge again.
        </p>
      )}
    </div>
  );
}

function ThresholdField({
  id, label, setting, value, options, onChange,
}: { id: string; label: string; setting: string; value: string; options: RoundOptions; onChange: (value: string) => void }) {
  const fallback = options.saturation_defaults[setting];
  return (
    <Field label={label} id={id}>
      <Input id={id} type="number" step="any" min="0" placeholder={fallback === undefined ? "Default" : `Default: ${fallback}`} value={value} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}
