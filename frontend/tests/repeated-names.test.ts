import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RepeatedNames, REPEATED_NAMES_SHOWN } from "@/components/repeated-names";
import { fromScreeningWarning, parseRepeatedName, splitRoundWarnings } from "@/lib/repeated-names";

// The API's wording (`enrichment/repeated_names.py`), trimmed to two parents.
const acrobat = "Repeated new speciality name 'adobe acrobat' across 2 distinct parents. "
  + "Parent Industry > Engineering (category ID 126): [923c: Industry > Engineering > Adobe Acrobat]; "
  + "Parent Industry > Installation, Maintenance, and Repair: [0e4f: Industry > Installation, Maintenance, and Repair > Adobe Acrobat]. "
  + "Advisory only; placements and approvals are unchanged.";
const roundWarning = (message: string) => `warning.repeated_speciality_name: ${message}`;

describe("repeated-name warnings", () => {
  it("reads the name as the proposals spell it and each parent without its category ID", () => {
    expect(parseRepeatedName(roundWarning(acrobat))).toEqual({
      level: "speciality",
      name: "Adobe Acrobat",
      parents: ["Industry > Engineering", "Industry > Installation, Maintenance, and Repair"],
      message: acrobat,
    });
  });

  it("handles a name the API quotes with double quotes because it holds an apostrophe", () => {
    const message = acrobat.replace("'adobe acrobat'", "\"moody's analytics\"").replaceAll("Adobe Acrobat", "Moody's Analytics");
    expect(parseRepeatedName(message)?.name).toBe("Moody's Analytics");
  });

  it("pulls repeated names out of a round's warnings, widest first, and leaves the rest alone", () => {
    const narrow = acrobat.replaceAll("acrobat", "xd").replaceAll("Acrobat", "XD").replace(/; Parent Industry > Inst.*?\]\./, ".");
    const other = "soc.csv changed after it was imported. Import the source again before running this round.";
    const { repeated, other: rest } = splitRoundWarnings([roundWarning(narrow), other, roundWarning(acrobat)]);
    expect(repeated.map((item) => [item.name, item.parents.length])).toEqual([["Adobe Acrobat", 2], ["Adobe XD", 1]]);
    expect(rest).toEqual([other]);
  });

  it("keeps a warning whose wording it doesn't recognise as an ordinary warning", () => {
    const changed = "warning.repeated_speciality_name: Navigation appears under Marine and Retail.";
    expect(splitRoundWarnings([changed]).other).toEqual([changed]);
    expect(fromScreeningWarning({
      code: "warning.repeated_speciality_name", name: "navigation", proposal_ids: [], message: "Navigation appears twice.",
    })).toEqual({ level: "speciality", name: "navigation", parents: [], message: "Navigation appears twice." });
  });

  it("lists a few names with parent counts and offers the rest behind Show all", () => {
    const names = Array.from({ length: REPEATED_NAMES_SHOWN + 2 }, (_, index) => ({
      level: "speciality" as const, name: `Tool ${index}`, parents: ["A > B", "A > C"], message: "",
    }));
    const html = renderToStaticMarkup(createElement(RepeatedNames, { names }));
    expect(html).toContain(`${names.length} new speciality names are proposed under more than one parent`);
    expect(html).toContain("Tool 0");
    expect(html).toContain("2 parents");
    expect(html).not.toContain(`Tool ${REPEATED_NAMES_SHOWN}`);
    expect(html).toContain(`Show all ${names.length}`);
    expect(renderToStaticMarkup(createElement(RepeatedNames, { names: [] }))).toBe("");
  });
});
