/**
 * The new-round form's logic, apart from its markup: which dropped files to upload, and turning what the form
 * holds into the `SourceMapping` and `RoundSettings` the API expects. The API checks everything again; catching
 * the obvious here lets the form say what's missing before anything is imported.
 */
import type { RoundFile, RoundSettings } from "@/lib/api/schemas";

export const UPLOADABLE_SUFFIXES = [".csv", ".tsv", ".xlsx", ".xlsm"];
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const ROUND_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const SOURCE_NAME = /^[a-z][a-z0-9_]*$/;
// Names the finished snapshot in the round's comparison; mirrors `RESERVED_SOURCE_NAME` in `app/rounds.py`.
const RESERVED_SOURCE_NAME = "enriched";

/** What the API calls the client's dumps when a round builds on them directly; mirrors `ORIGINAL_BASELINE`. */
export const ORIGINAL_BASELINE = "@original";

export function baselineLabel(baseline: string): string {
  return baseline === ORIGINAL_BASELINE ? "Original taxonomy" : baseline;
}

/** Why a file won't be uploaded, or null if it will. Folders bring along plenty that isn't a table. */
export function uploadProblem(file: { name: string; size: number }): string | null {
  const name = file.name.toLowerCase();
  if (file.name.startsWith(".")) return "hidden file";
  if (!UPLOADABLE_SUFFIXES.some((suffix) => name.endsWith(suffix))) return "not a CSV, TSV or Excel file";
  if (file.size > MAX_UPLOAD_BYTES) return "larger than 50 MB";
  return null;
}

export function roundIdProblem(roundId: string): string | null {
  return ROUND_ID.test(roundId) ? null : "Use lowercase letters, digits, - or _, starting with a letter or digit.";
}

/** A source name from a file's name: `Software Skills.xlsx` -> `software_skills`, valid or empty. */
export function suggestSourceName(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^[^a-z]+|_+$/g, "");
  return SOURCE_NAME.test(stem) && stem !== RESERVED_SOURCE_NAME ? stem : "";
}

/** One level of the hierarchy: a column as it stands, or a code's first characters named by a labels file. */
export type LevelDraft = { mode: "column" | "prefix"; column: string; prefix: string; labels: string };
export type FilterDraft = { column: string; values: string };

export type MappingDraft = {
  name: string;
  displayName: string;
  source: string;
  sheet: string;
  category: LevelDraft;
  speciality: LevelDraft;
  tag: LevelDraft;
  description: string;
  serviceMode: "id" | "column";
  serviceId: string;
  serviceColumn: string;
  filters: FilterDraft[];
  tablePrefix: string;
};

const emptyLevel = (): LevelDraft => ({ mode: "column", column: "", prefix: "2", labels: "" });

export function emptyMapping(): MappingDraft {
  return {
    name: "", displayName: "", source: "", sheet: "", category: emptyLevel(), speciality: emptyLevel(), tag: emptyLevel(),
    description: "", serviceMode: "id", serviceId: "", serviceColumn: "", filters: [], tablePrefix: "",
  };
}

const LEVELS = [["category", "Category"], ["speciality", "Speciality"], ["tag", "Tag"]] as const;

/** What still stops this mapping being imported, in the order the form asks for it. */
export function mappingProblems(draft: MappingDraft): string[] {
  const problems: string[] = [];
  if (!SOURCE_NAME.test(draft.name)) {
    problems.push("Source name must be lowercase letters, digits or _, starting with a letter.");
  }
  if (draft.name === RESERVED_SOURCE_NAME) {
    problems.push(`A source can't be named "${RESERVED_SOURCE_NAME}": the finished snapshot uses that name.`);
  }
  if (!draft.displayName.trim()) problems.push("Give the source a display name.");
  if (!draft.source) problems.push("Choose the file to import.");
  for (const [level, label] of LEVELS) {
    const spec = draft[level];
    if (!spec.column) problems.push(`${label}: choose a column.`);
    if (spec.mode === "prefix") {
      if (!/^[1-9]\d*$/.test(spec.prefix)) problems.push(`${label}: the code prefix length must be a whole number.`);
      if (!spec.labels) problems.push(`${label}: choose the labels file for its codes.`);
    }
  }
  if (draft.serviceMode === "id" && !draft.serviceId) problems.push("Choose the service its categories go under.");
  if (draft.serviceMode === "column" && !draft.serviceColumn) problems.push("Choose the column holding each row's service ID.");
  for (const filter of draft.filters) {
    if (!filter.column || !filterValues(filter.values).length) problems.push("Each filter needs a column and a value.");
  }
  return [...new Set(problems)];
}

/** Whether the names the form fills in from the file need attention, so the section holding them can open itself. */
export function sourceNameProblem(draft: MappingDraft): boolean {
  return !SOURCE_NAME.test(draft.name) || draft.name === RESERVED_SOURCE_NAME || !draft.displayName.trim();
}

export const SETUP_STEPS = [
  { id: "name", label: "Name" },
  { id: "files", label: "Files" },
  { id: "columns", label: "Columns" },
  { id: "options", label: "Options" },
  { id: "import", label: "Import & run" },
] as const;

/** The steps of a round that already exists; its name can't change afterwards. */
export type SetupStepId = Exclude<(typeof SETUP_STEPS)[number]["id"], "name">;

/** Whether a step's inputs exist yet: files before columns, and a complete mapping before anything after it. */
export function stepOpen(step: SetupStepId, readableFiles: number, problems: number): boolean {
  if (step === "files") return true;
  if (step === "columns") return readableFiles > 0;
  return readableFiles > 0 && problems === 0;
}

/** Where coming back to a round should land: the first step still needing something, or the import. */
export function firstStep(readableFiles: number, problems: number): SetupStepId {
  if (readableFiles === 0) return "files";
  return problems === 0 ? "import" : "columns";
}

/** The chosen step, unless a removed file or a cleared choice has since closed it. */
export function currentStep(chosen: SetupStepId, readableFiles: number, problems: number): SetupStepId {
  if (stepOpen(chosen, readableFiles, problems)) return chosen;
  return readableFiles > 0 ? "columns" : "files";
}

/** A workbook with a bad first sheet can still have a good one, so it stays choosable; a CSV that can't be read can't. */
export function readableFiles(files: RoundFile[]): RoundFile[] {
  return files.filter((file) => !file.error || file.sheets.length > 0);
}

// A category, speciality and tag each need a column, so a sheet with fewer can't be the table.
const MIN_TABLE_COLUMNS = 3;

/** The sheet to start on: the first wide enough to be a table, which skips a leading legend. "" means the first sheet. */
export function suggestSheet(file: RoundFile): string {
  const suggested = file.sheets.find((sheet) => (file.sheet_columns[sheet]?.length ?? 0) >= MIN_TABLE_COLUMNS);
  return suggested && suggested !== file.sheets[0] ? suggested : "";
}

function withoutColumns(draft: MappingDraft): MappingDraft {
  return {
    ...draft, category: emptyLevel(), speciality: emptyLevel(), tag: emptyLevel(), description: "", serviceColumn: "",
    filters: [],
  };
}

/** Moves to another file. Its columns differ, so earlier choices go; names follow unless someone typed their own. */
export function chooseFile(draft: MappingDraft, file: RoundFile): MappingDraft {
  const displayed = (name: string) => name.replace(/\.[^.]+$/, "");
  return {
    ...withoutColumns(draft),
    source: file.name,
    sheet: suggestSheet(file),
    name: !draft.name || draft.name === suggestSourceName(draft.source) ? suggestSourceName(file.name) : draft.name,
    displayName: !draft.displayName || draft.displayName === displayed(draft.source) ? displayed(file.name) : draft.displayName,
  };
}

/** Moves to another sheet, which has its own columns. "" keeps meaning the first sheet, as it does when saved. */
export function chooseSheet(draft: MappingDraft, file: RoundFile, sheet: string): MappingDraft {
  return { ...withoutColumns(draft), sheet: sheet === file.sheets[0] ? "" : sheet };
}

/** Chooses the file when there is only one to choose, so the step doesn't open by asking the obvious. */
export function withOnlyFile(draft: MappingDraft, files: RoundFile[]): MappingDraft {
  return !draft.source && files.length === 1 ? chooseFile(draft, files[0]) : draft;
}

/** What each chosen column is being used for, to mark it on the table preview. */
export function columnRoles(draft: MappingDraft): Record<string, string[]> {
  const roles: Record<string, string[]> = {};
  const mark = (column: string, role: string) => {
    if (column) (roles[column] ??= []).push(role);
  };
  for (const [level, label] of LEVELS) mark(draft[level].column, label);
  mark(draft.description, "Description");
  if (draft.serviceMode === "column") mark(draft.serviceColumn, "Service");
  for (const filter of draft.filters) mark(filter.column, "Filter");
  return roles;
}

function exampleValue(spec: LevelDraft, row: Record<string, string>): string {
  if (!spec.column) return "…";
  const value = row[spec.column] || "(blank)";
  if (spec.mode === "column") return value;
  const length = Number(spec.prefix);
  return Number.isInteger(length) && length > 0 ? `code ${value.slice(0, length)}` : `code ${value}`;
}

/** The first rows as the paths they'd become. A code stays a code: its label is in another file the form hasn't read. */
export function examplePaths(draft: MappingDraft, rows: Record<string, string>[], serviceName?: string): string[][] {
  if (!draft.category.column && !draft.speciality.column && !draft.tag.column) return [];
  const paths = rows.map((row) => [
    (draft.serviceMode === "id" ? serviceName : row[draft.serviceColumn]) || "…",
    exampleValue(draft.category, row), exampleValue(draft.speciality, row), exampleValue(draft.tag, row),
  ]);
  return [...new Map(paths.map((path) => [path.join(">"), path])).values()].slice(0, 3);
}

export function filterHasValue(values: string, value: string): boolean {
  return filterValues(values).includes(value);
}

/** Adds a value offered from the column to a filter, or takes it back out, keeping what was typed. */
export function toggleFilterValue(values: string, value: string): string {
  const current = filterValues(values);
  return filterText(current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
}

/** Comma-separated values, where a value holding a comma is written in double quotes. */
function filterValues(values: string): string[] {
  return (values.match(/\s*"[^"]*"|[^,]+/g) ?? [])
    .map((value) => value.trim().replace(/^"(.*)"$/, "$1").trim())
    .filter(Boolean);
}

function filterText(values: string | string[]): string {
  return (Array.isArray(values) ? values : [values]).map((value) => (value.includes(",") ? `"${value}"` : value)).join(", ");
}

function levelSpec(spec: LevelDraft): string | { column: string; prefix: number; labels: string } {
  return spec.mode === "column" ? spec.column : { column: spec.column, prefix: Number(spec.prefix), labels: spec.labels };
}

/** The `SourceMapping` the API takes. A filter with several comma-separated values keeps rows matching any of them. */
export function buildMapping(draft: MappingDraft): Record<string, unknown> {
  const filters = Object.fromEntries(draft.filters.map((filter) => {
    const values = filterValues(filter.values);
    return [filter.column, values.length === 1 ? values[0] : values];
  }));
  return {
    name: draft.name,
    display_name: draft.displayName.trim(),
    source: draft.source,
    ...(draft.sheet ? { sheet: draft.sheet } : {}),
    columns: {
      category: levelSpec(draft.category),
      speciality: levelSpec(draft.speciality),
      tag: levelSpec(draft.tag),
      ...(draft.description ? { tag_description: draft.description } : {}),
    },
    ...(draft.serviceMode === "id" ? { service_id: Number(draft.serviceId) } : { service_column: draft.serviceColumn }),
    filters,
    ...(draft.tablePrefix.trim() ? { table_prefix: draft.tablePrefix.trim() } : {}),
  };
}

function levelDraft(spec: unknown): LevelDraft {
  if (typeof spec === "string") return { ...emptyLevel(), column: spec };
  const derived = (spec ?? {}) as { column?: string; prefix?: number; labels?: string };
  if (!derived.column) return emptyLevel();
  return { mode: "prefix", column: derived.column, prefix: String(derived.prefix ?? 2), labels: derived.labels ?? "" };
}

/** The form's contents from a saved mapping, so coming back to a round doesn't mean starting over. */
export function draftFromMapping(saved: Record<string, unknown> | null): MappingDraft {
  if (!saved) return emptyMapping();
  const columns = (saved.columns ?? {}) as Record<string, unknown>;
  const filters = (saved.filters ?? {}) as Record<string, string | string[]>;
  const serviceColumn = typeof saved.service_column === "string" ? saved.service_column : "";
  return {
    name: String(saved.name ?? ""),
    displayName: String(saved.display_name ?? ""),
    source: String(saved.source ?? ""),
    sheet: typeof saved.sheet === "string" ? saved.sheet : "",
    category: levelDraft(columns.category),
    speciality: levelDraft(columns.speciality),
    tag: levelDraft(columns.tag),
    description: typeof columns.tag_description === "string" ? columns.tag_description : "",
    serviceMode: serviceColumn ? "column" : "id",
    serviceId: saved.service_id === undefined || saved.service_id === null ? "" : String(saved.service_id),
    serviceColumn,
    filters: Object.entries(filters).map(([column, values]) => ({
      column, values: filterText(values),
    })),
    tablePrefix: typeof saved.table_prefix === "string" ? saved.table_prefix : "",
  };
}

export type SettingsDraft = {
  judge: boolean;
  caseB: boolean;
  threshold: string;
  semanticThreshold: string;
  addLexical: string;
  addSemantic: string;
};

export const emptySettings = (): SettingsDraft => ({
  judge: false, caseB: false, threshold: "", semanticThreshold: "", addLexical: "", addSemantic: "",
});

const number = (value: string): number | null => (value.trim() === "" ? null : Number(value));

/** The `RoundSettings` the API takes; a threshold left blank keeps the template's value. */
export function buildSettings(draft: SettingsDraft): RoundSettings {
  return {
    judge: draft.judge,
    case_b: draft.caseB,
    threshold: number(draft.threshold),
    semantic_threshold: number(draft.semanticThreshold),
    add_candidate_lexical_threshold: number(draft.addLexical),
    add_candidate_semantic_threshold: number(draft.addSemantic),
  };
}

export function draftFromSettings(saved: RoundSettings): SettingsDraft {
  const text = (value: number | null) => (value === null ? "" : String(value));
  return {
    judge: saved.judge,
    caseB: saved.case_b,
    threshold: text(saved.threshold),
    semanticThreshold: text(saved.semantic_threshold),
    addLexical: text(saved.add_candidate_lexical_threshold),
    addSemantic: text(saved.add_candidate_semantic_threshold),
  };
}

/** Identifies what the form holds, to tell whether it's what was last imported. */
export function draftKey(mapping: MappingDraft, settings: SettingsDraft): string {
  return JSON.stringify([buildMapping(mapping), buildSettings(settings)]);
}

/** Everything in a dropped folder, however deep, as flat files. The browser hands entries over in batches. */
type DroppedEntry = {
  isFile: boolean;
  isDirectory: boolean;
  file?: (done: (file: File) => void, failed: (error: unknown) => void) => void;
  createReader?: () => { readEntries: (done: (entries: DroppedEntry[]) => void, failed: (error: unknown) => void) => void };
};

export async function filesFromEntries(entries: DroppedEntry[]): Promise<File[]> {
  const files: File[] = [];
  for (const entry of entries) {
    if (entry.isFile && entry.file) {
      // Bound, because a browser's entry methods throw unless called on the entry itself.
      const read = entry.file.bind(entry);
      files.push(await new Promise<File>((resolve, reject) => read(resolve, reject)));
    } else if (entry.isDirectory && entry.createReader) {
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise<DroppedEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
        if (batch.length === 0) break;
        files.push(...await filesFromEntries(batch));
      }
    }
  }
  return files;
}

/** A drop's files, with folders opened; entries must be read before the drop event returns, so this starts at once. */
export function droppedFiles(data: DataTransfer): Promise<File[]> {
  const entries = [...data.items]
    .map((item) => (item.kind === "file" ? (item.webkitGetAsEntry() as unknown as DroppedEntry | null) : null))
    .filter((entry): entry is DroppedEntry => entry !== null);
  return entries.length > 0 ? filesFromEntries(entries) : Promise.resolve([...data.files]);
}
