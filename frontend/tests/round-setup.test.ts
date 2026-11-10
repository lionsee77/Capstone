import { describe, expect, it } from "vitest";

import type { RoundFile } from "@/lib/api/schemas";
import {
  baselineLabel,
  buildMapping,
  buildSettings,
  chooseFile,
  chooseSheet,
  columnRoles,
  currentStep,
  examplePaths,
  filterHasValue,
  firstStep,
  sourceNameProblem,
  stepOpen,
  suggestSheet,
  toggleFilterValue,
  withOnlyFile,
  draftFromMapping,
  draftFromSettings,
  draftKey,
  emptyMapping,
  emptySettings,
  filesFromEntries,
  mappingProblems,
  type MappingDraft,
  MAX_UPLOAD_BYTES,
  roundIdProblem,
  suggestSourceName,
  uploadProblem,
} from "@/lib/round-setup";

const level = (column: string) => ({ mode: "column" as const, column, prefix: "2", labels: "" });
const onet = (): MappingDraft => ({
  ...emptyMapping(),
  name: "onet",
  displayName: "O*NET",
  source: "Software Skills.xlsx",
  category: { mode: "prefix", column: "O*NET-SOC Code", prefix: "2", labels: "soc_major_groups.csv" },
  speciality: level("Title"),
  tag: level("Workplace Example"),
  description: "Element Name",
  serviceId: "2",
  filters: [{ column: "Hot Technology", values: "Y" }],
  tablePrefix: "onet_",
});

describe("uploadProblem", () => {
  it("accepts the table types the importer reads, in any case", () => {
    for (const name of ["a.csv", "a.TSV", "a.xlsx", "A.XLSM"]) expect(uploadProblem({ name, size: 1 })).toBeNull();
  });

  it("says why it skips what a folder brings along", () => {
    expect(uploadProblem({ name: ".DS_Store", size: 1 })).toBe("hidden file");
    expect(uploadProblem({ name: "README.md", size: 1 })).toContain("not a CSV");
    expect(uploadProblem({ name: "big.csv", size: MAX_UPLOAD_BYTES + 1 })).toContain("50 MB");
  });
});

describe("baselineLabel", () => {
  it("names the original taxonomy in words, and a snapshot by its own name", () => {
    expect(baselineLabel("@original")).toBe("Original taxonomy");
    expect(baselineLabel("original")).toBe("original"); // a snapshot that happens to be called original
    expect(baselineLabel("judged-1015")).toBe("judged-1015");
  });
});

describe("names", () => {
  it("checks a round name the way the API does", () => {
    expect(roundIdProblem("onet-demo")).toBeNull();
    for (const name of ["Has Space", "UPPER", "-x", "a/b"]) expect(roundIdProblem(name)).not.toBeNull();
  });

  it("suggests a source name from a file name, or nothing it can't make valid", () => {
    expect(suggestSourceName("Software Skills.xlsx")).toBe("software_skills");
    expect(suggestSourceName("2024 Skills.csv")).toBe("skills");
    expect(suggestSourceName("123.csv")).toBe("");
    expect(suggestSourceName("Enriched.csv")).toBe("");
  });
});

describe("mappingProblems", () => {
  it("has nothing to say about a complete mapping", () => {
    expect(mappingProblems(onet())).toEqual([]);
  });

  it("lists what an empty form still needs", () => {
    const problems = mappingProblems(emptyMapping());
    expect(problems).toContain("Choose the file to import.");
    expect(problems).toContain("Tag: choose a column.");
    expect(problems).toContain("Choose the service its categories go under.");
  });

  it("refuses the finished snapshot's name, but not one merely containing it", () => {
    expect(mappingProblems({ ...onet(), name: "enriched" })[0]).toContain("finished snapshot");
    expect(mappingProblems({ ...onet(), name: "enriched_skills" })).toEqual([]);
  });

  it("requires a labels file and a whole prefix length for a derived level", () => {
    const draft = { ...onet(), category: { mode: "prefix" as const, column: "Code", prefix: "0", labels: "" } };
    expect(mappingProblems(draft)).toEqual(expect.arrayContaining([
      "Category: the code prefix length must be a whole number.", "Category: choose the labels file for its codes.",
    ]));
  });

  it("refuses a source name the importer would, and a half-filled filter", () => {
    expect(mappingProblems({ ...onet(), name: "O*NET" })[0]).toContain("Source name");
    expect(mappingProblems({ ...onet(), filters: [{ column: "Hot Technology", values: " , " }] }))
      .toContain("Each filter needs a column and a value.");
  });
});

describe("buildMapping", () => {
  it("produces the same mapping as the worked O*NET example", () => {
    expect(buildMapping(onet())).toEqual({
      name: "onet",
      display_name: "O*NET",
      source: "Software Skills.xlsx",
      columns: {
        category: { column: "O*NET-SOC Code", prefix: 2, labels: "soc_major_groups.csv" },
        speciality: "Title",
        tag: "Workplace Example",
        tag_description: "Element Name",
      },
      service_id: 2,
      filters: { "Hot Technology": "Y" },
      table_prefix: "onet_",
    });
  });

  it("uses a service column instead of an ID, and a list for several filter values", () => {
    const mapping = buildMapping({
      ...onet(), serviceMode: "column", serviceColumn: "Service", filters: [{ column: "Kind", values: "A, B" }],
    });
    expect(mapping.service_column).toBe("Service");
    expect(mapping).not.toHaveProperty("service_id");
    expect(mapping.filters).toEqual({ Kind: ["A", "B"] });
  });

  it("keeps a value holding a comma whole when it is quoted", () => {
    const mapping = buildMapping({ ...onet(), filters: [{ column: "Field", values: '"Business, Management", Law' }] });
    expect(mapping.filters).toEqual({ Field: ["Business, Management", "Law"] });
    const single = buildMapping({ ...onet(), filters: [{ column: "Field", values: '"Business, Management"' }] });
    expect(single.filters).toEqual({ Field: "Business, Management" });
  });

  it("writes such a value back with its quotes, so it survives coming back to the form", () => {
    for (const values of ['"Business, Management", Law', '"Business, Management"']) {
      const draft = { ...onet(), filters: [{ column: "Field", values }] };
      expect(buildMapping(draftFromMapping(buildMapping(draft)))).toEqual(buildMapping(draft));
    }
  });

  it("leaves out what wasn't filled in", () => {
    const mapping = buildMapping({ ...onet(), description: "", tablePrefix: " ", sheet: "" });
    expect(mapping).not.toHaveProperty("table_prefix");
    expect(mapping).not.toHaveProperty("sheet");
    expect(mapping.columns).not.toHaveProperty("tag_description");
  });

  it("comes back to the same form from a saved mapping", () => {
    expect(draftFromMapping(buildMapping(onet()))).toEqual(onet());
    expect(draftFromMapping(null)).toEqual(emptyMapping());
  });
});

describe("settings", () => {
  it("sends blank thresholds as unset and typed ones as numbers", () => {
    const settings = buildSettings({ ...emptySettings(), judge: true, threshold: "75", addSemantic: "0.5" });
    expect(settings).toEqual({
      judge: true, case_b: false, threshold: 75, semantic_threshold: null,
      add_candidate_lexical_threshold: null, add_candidate_semantic_threshold: 0.5,
    });
    expect(draftFromSettings(settings)).toEqual({ ...emptySettings(), judge: true, threshold: "75", addSemantic: "0.5" });
  });
});

describe("draftKey", () => {
  it("is the same for a saved round read back as for what was imported", () => {
    const settings = { ...emptySettings(), threshold: "75" };
    const saved = draftKey(draftFromMapping(buildMapping(onet())), draftFromSettings(buildSettings(settings)));
    expect(saved).toBe(draftKey(onet(), settings));
  });

  it("changes when a setting or the mapping does, so an old config can't run by mistake", () => {
    const imported = draftKey(onet(), emptySettings());
    expect(draftKey(onet(), { ...emptySettings(), judge: true })).not.toBe(imported);
    expect(draftKey({ ...onet(), serviceId: "3" }, emptySettings())).not.toBe(imported);
  });
});

describe("filesFromEntries", () => {
  const file = (name: string) => new File(["x"], name);
  /** Like a browser's entry, whose `file` method throws unless it's called on the entry itself. */
  class FileEntry {
    isFile = true;
    isDirectory = false;
    constructor(private readonly filename: string) {}
    file(done: (value: File) => void) {
      if (!(this instanceof FileEntry)) throw new TypeError("Illegal invocation");
      done(file(this.filename));
    }
  }
  const fileEntry = (name: string) => new FileEntry(name);
  const folder = (batches: ReturnType<typeof fileEntry>[][]) => ({
    isFile: false,
    isDirectory: true,
    createReader: () => {
      const remaining = [...batches, []];
      return { readEntries: (done: (entries: never[]) => void) => done(remaining.shift() as never[]) };
    },
  });

  it("opens folders, reading every batch the browser hands over", async () => {
    const files = await filesFromEntries([
      fileEntry("top.csv"),
      folder([[fileEntry("a.csv"), fileEntry("b.csv")], [fileEntry("c.csv")]]),
    ]);
    expect(files.map((item) => item.name)).toEqual(["top.csv", "a.csv", "b.csv", "c.csv"]);
  });

  it("opens folders inside folders", async () => {
    const nested = folder([[folder([[fileEntry("deep.csv")]]) as never]]);
    expect((await filesFromEntries([nested])).map((item) => item.name)).toEqual(["deep.csv"]);
  });
});

describe("the setup steps", () => {
  it("opens each step only once what it needs exists", () => {
    expect(stepOpen("files", 0, 5)).toBe(true);
    expect(stepOpen("columns", 0, 5)).toBe(false);
    expect(stepOpen("columns", 1, 5)).toBe(true);
    expect(stepOpen("options", 1, 5)).toBe(false);
    expect(stepOpen("import", 1, 0)).toBe(true);
  });

  it("resumes a round at the first step still needing something", () => {
    expect(firstStep(0, 5)).toBe("files");
    expect(firstStep(1, 5)).toBe("columns");
    expect(firstStep(1, 0)).toBe("import");
  });

  it("falls back when a removed file closes the step on screen", () => {
    expect(currentStep("import", 1, 0)).toBe("import");
    expect(currentStep("import", 1, 2)).toBe("columns");
    expect(currentStep("options", 0, 2)).toBe("files");
  });
});

describe("choosing a file and sheet", () => {
  const file = (overrides: Partial<RoundFile> = {}): RoundFile => ({
    name: "Software Skills.xlsx", size: 1, sheets: ["Sheet1"], columns: [], row_count: 0, samples: {}, preview_rows: [],
    sheet_columns: {}, error: null, ...overrides,
  });

  it("skips a leading legend when suggesting a sheet", () => {
    const sheet_columns = { Legend: ["Notes"], Data: ["Sector", "Role", "Skill"] };
    expect(suggestSheet(file({ sheets: ["Legend", "Data"], sheet_columns }))).toBe("Data");
  });

  it("stays on the first sheet when it is a table, or when no sheet looks like one", () => {
    expect(suggestSheet(file({ sheets: ["A", "B"], sheet_columns: { A: ["1", "2", "3"], B: ["1", "2", "3"] } }))).toBe("");
    expect(suggestSheet(file({ sheets: ["A", "B"], sheet_columns: { A: ["1"], B: ["1"] } }))).toBe("");
    expect(suggestSheet(file())).toBe("");
  });

  it("clears column choices that belong to the old file, and fills in names from the new one", () => {
    const draft = { ...emptyMapping(), source: "old.csv", name: "old", displayName: "old", description: "Desc" };
    draft.category = { ...draft.category, column: "Sector" };
    draft.filters = [{ column: "Hot", values: "Y" }];

    const next = chooseFile(draft, file());

    expect(next).toMatchObject({ source: "Software Skills.xlsx", name: "software_skills", displayName: "Software Skills", description: "", filters: [] });
    expect(next.category.column).toBe("");
  });

  it("keeps names someone typed when the file changes", () => {
    const draft = { ...emptyMapping(), source: "old.csv", name: "mine", displayName: "My source" };
    expect(chooseFile(draft, file())).toMatchObject({ name: "mine", displayName: "My source" });
  });

  it("clears column choices when the sheet changes, and writes the first sheet as blank", () => {
    const uploaded = file({ sheets: ["A", "B"] });
    const draft = { ...emptyMapping(), sheet: "B", serviceColumn: "Svc" };
    expect(chooseSheet(draft, uploaded, "A")).toMatchObject({ sheet: "", serviceColumn: "" });
    expect(chooseSheet(draft, uploaded, "B").sheet).toBe("B");
  });

  it("chooses the file only when there is just the one", () => {
    expect(withOnlyFile(emptyMapping(), [file()]).source).toBe("Software Skills.xlsx");
    expect(withOnlyFile(emptyMapping(), [file(), file({ name: "other.csv" })]).source).toBe("");
    expect(withOnlyFile({ ...emptyMapping(), source: "saved.csv" }, [file()]).source).toBe("saved.csv");
  });
});

describe("previewing the mapping", () => {
  const draft = (): MappingDraft => {
    const mapping = { ...emptyMapping(), serviceId: "1" };
    mapping.category = { ...mapping.category, column: "Code", mode: "prefix", prefix: "2", labels: "labels.csv" };
    mapping.speciality = { ...mapping.speciality, column: "Role" };
    return mapping;
  };

  it("marks what each chosen column is for, including a column used twice", () => {
    const mapping = { ...draft(), description: "Role", filters: [{ column: "Hot", values: "Y" }] };
    expect(columnRoles(mapping)).toEqual({ Code: ["Category"], Role: ["Speciality", "Description"], Hot: ["Filter"] });
  });

  it("shows rows as paths, with codes cut to their prefix and the unchosen as a gap", () => {
    const rows = [{ Code: "15-1252", Role: "Developer" }, { Code: "15-1252", Role: "Developer" }, { Code: "13-2011", Role: "" }];
    expect(examplePaths(draft(), rows, "Function")).toEqual([
      ["Function", "code 15", "Developer", "…"],
      ["Function", "code 13", "(blank)", "…"],
    ]);
  });

  it("shows nothing until a column is chosen", () => {
    expect(examplePaths(emptyMapping(), [{ A: "1" }], "Function")).toEqual([]);
  });

  it("toggles a filter value, keeping values with commas quoted", () => {
    expect(toggleFilterValue("", "Y")).toBe("Y");
    expect(toggleFilterValue("Y", "Sales, retail")).toBe('Y, "Sales, retail"');
    expect(toggleFilterValue('Y, "Sales, retail"', "Y")).toBe('"Sales, retail"');
    expect(filterHasValue('Y, "Sales, retail"', "Sales, retail")).toBe(true);
    expect(filterHasValue("Y", "N")).toBe(false);
  });

  it("asks to look at the source names only when they're unusable", () => {
    expect(sourceNameProblem({ ...emptyMapping(), name: "onet", displayName: "O*NET" })).toBe(false);
    expect(sourceNameProblem({ ...emptyMapping(), name: "enriched", displayName: "E" })).toBe(true);
    expect(sourceNameProblem({ ...emptyMapping(), name: "onet", displayName: " " })).toBe(true);
  });
});
