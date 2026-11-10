import { describe, expect, it } from "vitest";

import type { LookupMatch, TagLookupResult } from "@/lib/api/schemas";
import { isLookupTerm, lookupVerdict, matchLabel, parentLabel } from "@/lib/lookup";

const match = (overrides: Partial<LookupMatch> = {}): LookupMatch => ({
  id: 1, name: "Tax", level: "tags", match_type: "exact",
  lexical_score: 100, semantic_score: null, active: true, paths: [],
  ...overrides,
});
const result = (overrides: Partial<TagLookupResult> = {}): TagLookupResult => ({
  query: "tax", snapshot: "onet-1031", exists: false, exact: [], near: [],
  ...overrides,
});

describe("isLookupTerm", () => {
  it("accepts a term with a letter or digit, in any script", () => {
    for (const term of ["tax", "C++", "5G", "数据"]) expect(isLookupTerm(term)).toBe(true);
  });

  it("refuses what the API would reject", () => {
    for (const term of ["", "   ", "?!-", "x".repeat(201)]) expect(isLookupTerm(term)).toBe(false);
  });
});

describe("lookupVerdict", () => {
  it("says the term exists when there is an exact match, even with close ones too", () => {
    const verdict = lookupVerdict(result({ exists: true, exact: [match()], near: [match()] }));
    expect(verdict.kind).toBe("exists");
  });

  it("points at close matches when nothing is exact", () => {
    expect(lookupVerdict(result({ near: [match({ match_type: "lexical" })] })).kind).toBe("close");
  });

  it("says the term looks new when nothing matches", () => {
    expect(lookupVerdict(result()).kind).toBe("new");
  });

  it("always names the snapshot that answered", () => {
    expect(lookupVerdict(result()).detail).toContain("onet-1031");
    expect(lookupVerdict(result({ exists: true, exact: [match()] })).detail).toContain("onet-1031");
  });
});

describe("matchLabel", () => {
  it("describes each way of matching, with the score behind a near match", () => {
    expect(matchLabel(match())).toBe("Exact");
    expect(matchLabel(match({ match_type: "variant" }))).toBe("Spelling variant");
    expect(matchLabel(match({ match_type: "lexical", lexical_score: 94.74 }))).toBe("Similar spelling (94.7 / 100)");
    expect(matchLabel(match({ match_type: "semantic", semantic_score: 0.7433 }))).toBe("Similar meaning (0.74 / 1)");
    expect(matchLabel(match({ match_type: "synonym", synonym: "GST" }))).toBe('Approved synonym "GST"');
  });
});

describe("parentLabel", () => {
  const service = { id: 1, name: "Industry" };
  const category = { id: 2, name: "Finance" };
  const speciality = { id: 3, name: "Tax Advisory" };

  it("lists every parent of a tag", () => {
    expect(parentLabel({ service, category, speciality }, "tags")).toBe("Industry › Finance › Tax Advisory");
  });

  it("leaves out the concept itself", () => {
    expect(parentLabel({ service, category, speciality }, "specialities")).toBe("Industry › Finance");
    expect(parentLabel({ service, category: null, speciality: null }, "services")).toBe("");
  });

  it("skips a step an orphan row is missing", () => {
    expect(parentLabel({ service: null, category: null, speciality }, "specialities")).toBe("");
    expect(parentLabel({ service, category: null, speciality }, "tags")).toBe("Industry › Tax Advisory");
  });
});
