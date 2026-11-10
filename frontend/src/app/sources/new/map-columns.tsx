"use client";

import { useState } from "react";

import { Notice } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import type { RoundFile, RoundOptions } from "@/lib/api/schemas";
import {
  chooseFile,
  chooseSheet,
  columnRoles,
  examplePaths,
  type FilterDraft,
  filterHasValue,
  type LevelDraft,
  type MappingDraft,
  sourceNameProblem,
  toggleFilterValue,
} from "@/lib/round-setup";

/**
 * The step that says how to read a table. The table's own first rows are on screen, with each chosen column marked
 * and a worked example underneath, so a reviewer checks a choice against the data rather than against a column name.
 */
export function MapColumns({
  mapping, onChange, table, tableError, files, options,
}: {
  mapping: MappingDraft;
  onChange: (next: MappingDraft) => void;
  table: RoundFile | undefined;
  tableError?: string;
  files: RoundFile[];
  options: RoundOptions;
}) {
  const update = (patch: Partial<MappingDraft>) => onChange({ ...mapping, ...patch });
  const uploaded = files.find((file) => file.name === mapping.source);
  const sheets = uploaded?.sheets ?? [];
  const serviceName = options.services.find((service) => String(service.id) === mapping.serviceId)?.name;
  const examples = table ? examplePaths(mapping, table.preview_rows, serviceName) : [];
  const nameProblem = sourceNameProblem(mapping);
  const [moreOpen, setMoreOpen] = useState(Boolean(mapping.description || mapping.tablePrefix));

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="File to read" id="map-source">
          <NativeSelect
            id="map-source"
            value={mapping.source}
            onChange={(e) => {
              const file = files.find((item) => item.name === e.target.value);
              if (file) onChange(chooseFile(mapping, file));
            }}
          >
            {!mapping.source && <option value="">Choose a file</option>}
            {files.map((file) => <option key={file.name} value={file.name}>{file.name}</option>)}
          </NativeSelect>
        </Field>
        {uploaded && sheets.length > 1 && (
          <Field label="Sheet" id="map-sheet">
            <NativeSelect
              id="map-sheet"
              value={mapping.sheet || sheets[0]}
              onChange={(e) => onChange(chooseSheet(mapping, uploaded, e.target.value))}
            >
              {sheets.map((sheet) => {
                const columns = uploaded.sheet_columns[sheet]?.length;
                return <option key={sheet} value={sheet}>{sheet}{columns === undefined ? "" : ` (${columns} columns)`}</option>;
              })}
            </NativeSelect>
          </Field>
        )}
      </div>

      {tableError && <Notice title="This sheet could not be loaded" tone="error">{tableError}</Notice>}
      {table?.error && <Notice title="This file or sheet can't be read" tone="error">{table.error}</Notice>}
      {mapping.source && !table && !tableError && <Skeleton className="h-40 w-full" />}

      {table && !table.error && (
        <>
          <TablePreview table={table} mapping={mapping} />

          <div className="grid gap-4 md:grid-cols-3">
            <LevelFields label="Category" hint="The broad group each row belongs to." value={mapping.category} onChange={(category) => update({ category })} table={table} files={files} />
            <LevelFields label="Speciality" hint="The role or area within a category." value={mapping.speciality} onChange={(speciality) => update({ speciality })} table={table} files={files} />
            <LevelFields label="Tag" hint="The skill or term itself." value={mapping.tag} onChange={(tag) => update({ tag })} table={table} files={files} />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Where these categories go" id="map-service-mode">
              <NativeSelect id="map-service-mode" value={mapping.serviceMode} onChange={(e) => update({ serviceMode: e.target.value as MappingDraft["serviceMode"] })}>
                <option value="id">All under one service</option>
                <option value="column">Each row names its service ID</option>
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

          {examples.length > 0 && (
            <div className="rounded-md bg-muted/50 p-3 text-sm" data-testid="example-paths">
              <p className="font-medium">How the first rows will read</p>
              <ul className="mt-1 flex flex-col gap-0.5">
                {examples.map((path) => <li key={path.join(">")}>{path.join(" › ")}</li>)}
              </ul>
              {mapping.filters.length > 0 && <p className="mt-1 text-muted-foreground">Shown before your row filters apply.</p>}
            </div>
          )}

          <Filters filters={mapping.filters} table={table} onChange={(filters) => update({ filters })} />

          <details
            className="rounded-md border border-border p-3"
            open={moreOpen || nameProblem}
            onToggle={(e) => setMoreOpen(e.currentTarget.open)}
          >
            <summary className="cursor-pointer text-sm font-medium">More options: description and source name</summary>
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              <Field label="Tag description (optional)" id="map-description" hint="Extra context for classifying a tag.">
                <ColumnSelect id="map-description" table={table} value={mapping.description} onChange={(description) => update({ description })} empty="None" />
              </Field>
              <Field label="Display name" id="map-display" hint="How the LLM prompt names this source. Filled in from the file name.">
                <Input id="map-display" value={mapping.displayName} onChange={(e) => update({ displayName: e.target.value })} />
              </Field>
              <Field label="Source name" id="map-name" hint="Names its tables and judge cache: lowercase, digits and _. Filled in from the file name.">
                <Input id="map-name" value={mapping.name} onChange={(e) => update({ name: e.target.value })} />
              </Field>
              <Field label="Table prefix (optional)" id="map-prefix" hint="Defaults to the source name followed by _.">
                <Input id="map-prefix" value={mapping.tablePrefix} onChange={(e) => update({ tablePrefix: e.target.value })} />
              </Field>
            </div>
          </details>
        </>
      )}
    </div>
  );
}

function Field({ label, id, hint, children }: { label: string; id: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
    </div>
  );
}

function TablePreview({ table, mapping }: { table: RoundFile; mapping: MappingDraft }) {
  const roles = columnRoles(mapping);
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-sm text-muted-foreground">
        The first rows of {table.row_count.toLocaleString()}. Columns you choose below are marked here.
      </p>
      <div className="overflow-x-auto rounded-md border border-border" data-testid="table-preview">
        <table className="w-full text-left text-sm">
          <thead className="bg-muted/50">
            <tr>
              {table.columns.map((column) => (
                <th key={column} scope="col" className={`px-3 py-2 align-bottom font-medium whitespace-nowrap ${roles[column] ? "bg-accent" : ""}`}>
                  {roles[column] && <span className="block text-xs font-normal text-primary">{roles[column].join(", ")}</span>}
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {table.preview_rows.map((row, index) => (
              <tr key={index}>
                {table.columns.map((column) => (
                  <td key={column} title={row[column]} className={`max-w-56 truncate px-3 py-1.5 ${roles[column] ? "bg-accent/40" : ""}`}>{row[column]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ColumnSelect({
  id, table, value, onChange, empty = "Choose a column",
}: { id: string; table: RoundFile; value: string; onChange: (value: string) => void; empty?: string }) {
  return (
    <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{empty}</option>
      {table.columns.map((column) => <option key={column} value={column}>{column}</option>)}
    </NativeSelect>
  );
}

function LevelFields({
  label, hint, value, onChange, table, files,
}: { label: string; hint: string; value: LevelDraft; onChange: (value: LevelDraft) => void; table: RoundFile; files: RoundFile[] }) {
  const id = label.toLowerCase();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Field label={label} id={`map-${id}`} hint={hint}>
        <ColumnSelect id={`map-${id}`} table={table} value={value.column} onChange={(column) => onChange({ ...value, column })} />
      </Field>
      <label className="flex items-start gap-2 text-sm">
        <input
          id={`map-${id}-mode`}
          type="checkbox"
          className="mt-1"
          checked={value.mode === "prefix"}
          onChange={(e) => onChange({ ...value, mode: e.target.checked ? "prefix" : "column" })}
        />
        <span>This column holds codes, named by a labels file</span>
      </label>
      {value.mode === "prefix" && (
        <div className="flex flex-col gap-3 rounded-md bg-muted/50 p-3">
          <Field label="Characters to use" id={`map-${id}-prefix`} hint="How many of the code's first characters name the group: 2 turns 15-1252 into 15.">
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
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm font-medium">Keep only some rows? (optional)</p>
        <Button size="sm" variant="outline" onClick={() => onChange([...filters, { column: "", values: "" }])}>Add a filter</Button>
      </div>
      {filters.map((filter, index) => (
        <div key={index} className="flex flex-col gap-2 rounded-md bg-muted/50 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="min-w-48 flex-1">
              <ColumnSelect id={`map-filter-${index}`} table={table} value={filter.column} onChange={(column) => change(index, { column, values: "" })} />
            </div>
            <span className="text-sm text-muted-foreground">is</span>
            <Input
              aria-label={`Values for filter ${index + 1}`}
              className="max-w-56 bg-background"
              placeholder='Value, several with commas'
              value={filter.values}
              onChange={(e) => change(index, { values: e.target.value })}
            />
            <Button size="xs" variant="ghost" onClick={() => onChange(filters.filter((_, position) => position !== index))}>Remove</Button>
          </div>
          {(table.samples[filter.column] ?? []).length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-sm">
              <span className="text-muted-foreground">Values in this column:</span>
              {table.samples[filter.column].map((sample) => (
                <Button
                  key={sample}
                  size="xs"
                  variant={filterHasValue(filter.values, sample) ? "secondary" : "outline"}
                  aria-pressed={filterHasValue(filter.values, sample)}
                  onClick={() => change(index, { values: toggleFilterValue(filter.values, sample) })}
                >
                  {sample}
                </Button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
