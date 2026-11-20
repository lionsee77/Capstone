"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

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
  baselineLabel,
  buildMapping,
  buildSettings,
  currentStep,
  draftFromMapping,
  draftFromSettings,
  draftKey,
  droppedFiles,
  firstStep,
  type MappingDraft,
  mappingProblems,
  ORIGINAL_BASELINE,
  readableFiles,
  roundIdProblem,
  type SettingsDraft,
  SETUP_STEPS,
  type SetupStepId,
  stepOpen,
  uploadProblem,
  withOnlyFile,
} from "@/lib/round-setup";

import { MapColumns } from "./map-columns";

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

/** Where the round is up to. A step can be revisited once what it needs is in place. */
function Stepper({
  current, isOpen, onChoose,
}: { current: "name" | SetupStepId; isOpen: (step: SetupStepId) => boolean; onChoose: (step: SetupStepId) => void }) {
  const position = SETUP_STEPS.findIndex((step) => step.id === current);
  return (
    <ol aria-label="Steps" className="flex flex-wrap items-center gap-x-1 gap-y-2">
      {SETUP_STEPS.map((step, index) => {
        const active = step.id === current;
        const done = index < position;
        const circle = active
          ? "bg-primary text-primary-foreground"
          : done ? "bg-accent text-foreground" : "bg-muted text-muted-foreground";
        const content = (
          <>
            <span aria-hidden className={`flex size-6 items-center justify-center rounded-full text-xs font-medium ${circle}`}>
              {done ? "✓" : index + 1}
            </span>
            <span className={active ? "font-medium" : "text-muted-foreground"}>{step.label}</span>
          </>
        );
        return (
          <li key={step.id} aria-current={active ? "step" : undefined} className="flex items-center gap-1 text-sm">
            {index > 0 && <span aria-hidden className="mx-1 hidden h-px w-6 bg-rule sm:block" />}
            {step.id !== "name" && !active && isOpen(step.id) ? (
              <button type="button" className="flex items-center gap-2 rounded-md px-1 py-0.5 hover:bg-muted" onClick={() => onChoose(step.id as SetupStepId)}>
                {content}
              </button>
            ) : (
              <span className="flex items-center gap-2 px-1 py-0.5">{content}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function StepTitle({ number, children }: { number: number; children: string }) {
  return <>Step {number} of {SETUP_STEPS.length}: {children}</>;
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
    <div className="flex flex-col gap-8">
      <Stepper current="name" isOpen={() => false} onChoose={() => undefined} />
      <Section title={<StepTitle number={1}>Name the round</StepTitle>}>
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
                <option key={snapshot} value={snapshot}>{baselineLabel(snapshot)}</option>
              ))}
            </NativeSelect>
            <p className="max-w-64 text-sm text-muted-foreground">
              {baseline === ORIGINAL_BASELINE
                ? "Before any enrichment. A source is read from one table, so SkillsFuture gives framework skill titles, not Unique Skills, and review will be long."
                : "The newest is first. New tags are checked against this version."}
            </p>
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
            Next: add files
          </Button>
        </div>
        {create.error && <p className="text-sm text-destructive">{create.error.message}</p>}
      </Section>
    </div>
  );
}

function SetupRound({ roundId, options }: { roundId: string; options: RoundOptions }) {
  const { data: round, error } = useRound(roundId);
  const { data: files, error: filesError, refetch: refetchFiles, isFetching: fetchingFiles } = useRoundFiles(roundId);
  if (error) {
    return (
      <Notice title="Could not load this round" tone="error">
        {error.message}
      </Notice>
    );
  }
  // Without this the page would wait for the file list forever, with no hint why.
  if (filesError && !files) {
    return (
      <Notice title="Could not list this round's files" tone="error">
        <p>{filesError.message}</p>
        <Button className="mt-2" size="sm" variant="outline" disabled={fetchingFiles} onClick={() => void refetchFiles()}>
          {fetchingFiles ? "Trying again…" : "Try again"}
        </Button>
      </Notice>
    );
  }
  // Waits for the files too: which step to resume on depends on them.
  if (!round || !files) return <Skeleton className="h-40 w-full" />;
  return <Wizard key={round.round_id} round={round} files={files} options={options} />;
}

function Wizard({ round, files, options }: { round: RoundState; files: RoundFile[]; options: RoundOptions }) {
  const router = useRouter();
  const readable = readableFiles(files);
  const [mapping, setMapping] = useState<MappingDraft>(() => withOnlyFile(draftFromMapping(round.mapping), readable));
  const [settings, setSettings] = useState<SettingsDraft>(() => draftFromSettings(round.settings));
  const problems = mappingProblems(mapping);
  const [chosenStep, setChosenStep] = useState<SetupStepId>(() => firstStep(readable.length, problems.length));
  const step = currentStep(chosenStep, readable.length, problems.length);
  const configure = useConfigureRound(round.round_id);
  const run = useRunRound(round.round_id);

  const saved = round.status === "ready" && !configure.isPending;
  // What's on screen must be what would run: change anything after importing and it has to be imported again.
  const currentKey = draftKey(mapping, settings);
  const [importedKey, setImportedKey] = useState(() => draftKey(draftFromMapping(round.mapping), draftFromSettings(round.settings)));
  const upToDate = saved && currentKey === importedKey;

  const { data: chosen, error: tableError } = useRoundFile(round.round_id, mapping.source || null, mapping.sheet || null);
  const uploaded = files.find((file) => file.name === mapping.source);
  // The upload describes the first sheet only, so another sheet waits for its own answer.
  const table = chosen ?? (mapping.sheet ? undefined : uploaded);

  function goTo(next: SetupStepId) {
    if (next === "columns") setMapping((current) => withOnlyFile(current, readable));
    setChosenStep(next);
  }

  return (
    <div className="flex flex-col gap-8">
      <Stepper current={step} isOpen={(id) => stepOpen(id, readable.length, problems.length)} onChoose={goTo} />
      <p className="text-sm text-muted-foreground">
        Round <span className="font-medium text-foreground">{round.round_id}</span>, built on {baselineLabel(round.baseline_snapshot)}.
      </p>

      {step === "files" && (
        <Section title={<StepTitle number={2}>Add the source files</StepTitle>}>
          <AddFiles
            round={round}
            files={files}
            onRemoved={(name) => setMapping((current) => (current.source === name ? { ...current, source: "", sheet: "" } : current))}
          />
          <StepButtons nextLabel="Next: match columns" canContinue={readable.length > 0} onNext={() => goTo("columns")} />
        </Section>
      )}

      {step === "columns" && (
        <Section title={<StepTitle number={3}>Match the columns</StepTitle>}>
          <MapColumns mapping={mapping} onChange={setMapping} table={table} tableError={tableError?.message} files={readable} options={options} />
          {problems.length > 0 && (
            <div className="text-sm text-muted-foreground">
              <p className="font-medium">Still needed</p>
              <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4">
                {problems.map((problem) => <li key={problem}>{problem}</li>)}
              </ul>
            </div>
          )}
          <StepButtons onBack={() => goTo("files")} nextLabel="Next: options" canContinue={problems.length === 0} onNext={() => goTo("options")} />
        </Section>
      )}

      {step === "options" && (
        <Section title={<StepTitle number={4}>Choose the options</StepTitle>}>
          <p className="text-sm text-muted-foreground">The defaults suit most sources, so you can usually continue without changing anything.</p>
          <LlmSwitches settings={settings} options={options} tags={table?.row_count} onChange={setSettings} />
          <details className="rounded-md border border-border p-3">
            <summary className="cursor-pointer text-sm font-medium">Matching thresholds (advanced)</summary>
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              <ThresholdField id="set-threshold" label="Duplicate spelling score (0-100)" setting="threshold" value={settings.threshold} options={options} onChange={(threshold) => setSettings({ ...settings, threshold })} />
              <ThresholdField id="set-semantic" label="Duplicate meaning score (0-1)" setting="semantic_threshold" value={settings.semanticThreshold} options={options} onChange={(semanticThreshold) => setSettings({ ...settings, semanticThreshold })} />
              <ThresholdField id="set-add-lexical" label="Clearly new below spelling score (0-100)" setting="add_candidate_lexical_threshold" value={settings.addLexical} options={options} onChange={(addLexical) => setSettings({ ...settings, addLexical })} />
              <ThresholdField id="set-add-semantic" label="Clearly new below meaning score (0-1)" setting="add_candidate_semantic_threshold" value={settings.addSemantic} options={options} onChange={(addSemantic) => setSettings({ ...settings, addSemantic })} />
            </div>
          </details>
          <StepButtons onBack={() => goTo("columns")} nextLabel="Next: import" canContinue onNext={() => goTo("import")} />
        </Section>
      )}

      {step === "import" && (
        <Section title={<StepTitle number={5}>Import and run</StepTitle>}>
          <SetupSummary mapping={mapping} settings={settings} options={options} />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" onClick={() => goTo("options")}>Back</Button>
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
            {upToDate && (
              <Button
                disabled={run.isPending}
                onClick={() => run.mutate(undefined, { onSuccess: () => router.push(`/rounds/${encodeURIComponent(round.round_id)}`) })}
              >
                Run the pipeline
              </Button>
            )}
          </div>
          {configure.error && (
            <Notice title="The source could not be imported" tone="error">
              {configure.error.message}
            </Notice>
          )}
          {round.status === "setup" && round.warnings.length > 0 && (
            <Notice title="Import again before running">
              {round.warnings.join(" ")}
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
          {run.error && <p className="text-sm text-destructive">{run.error.message}</p>}
        </Section>
      )}
    </div>
  );
}

function StepButtons({
  onBack, nextLabel, canContinue, onNext,
}: { onBack?: () => void; nextLabel: string; canContinue: boolean; onNext: () => void }) {
  return (
    <div className="flex items-center gap-2">
      {onBack && <Button variant="ghost" onClick={onBack}>Back</Button>}
      <Button disabled={!canContinue} onClick={onNext}>{nextLabel}</Button>
    </div>
  );
}

function AddFiles({ round, files, onRemoved }: { round: RoundState; files: RoundFile[]; onRemoved: (name: string) => void }) {
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
    <>
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
                  {file.sheets.length > 1 ? `, ${file.sheets.length} sheets` : ""}
                </span>
              </span>
              <Button
                size="xs"
                variant="ghost"
                disabled={remove.isPending}
                onClick={() => remove.mutate(file.name, { onSuccess: () => onRemoved(file.name) })}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      {remove.error && <p className="text-sm text-destructive">{remove.error.message}</p>}
    </>
  );
}

/** What the import will use, in one place, so the last check doesn't mean going back through the steps. */
function SetupSummary({ mapping, settings, options }: { mapping: MappingDraft; settings: SettingsDraft; options: RoundOptions }) {
  const level = (spec: MappingDraft["category"]) => (spec.mode === "column" ? spec.column : `${spec.column} (first ${spec.prefix} characters, named by ${spec.labels})`);
  const service = options.services.find((item) => String(item.id) === mapping.serviceId)?.name;
  const llm = [settings.judge && "LLM judge", settings.caseB && "LLM classification of tags without a parent"].filter(Boolean);
  const rows: [string, string][] = [
    ["File", mapping.sheet ? `${mapping.source}, sheet ${mapping.sheet}` : mapping.source],
    ["Category", level(mapping.category)],
    ["Speciality", level(mapping.speciality)],
    ["Tag", level(mapping.tag)],
    ["Goes under", mapping.serviceMode === "id" ? (service ?? mapping.serviceId) : `The service ID in ${mapping.serviceColumn}`],
    ["Rows kept", mapping.filters.length ? mapping.filters.map((filter) => `${filter.column} is ${filter.values}`).join("; ") : "All"],
    ["LLM steps", llm.length ? llm.join(", ") : "Off"],
  ];
  return (
    <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-muted-foreground">{term}</dt>
          <dd className="min-w-0 break-words">{value}</dd>
        </div>
      ))}
    </dl>
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
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="number" step="any" min="0" placeholder={fallback === undefined ? "Default" : `Default: ${fallback}`} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
