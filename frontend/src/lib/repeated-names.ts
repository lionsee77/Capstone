/**
 * Repeated-name warnings, regrouped for reading.
 *
 * The API reports a new category or speciality name proposed under several parents as one long sentence
 * (`enrichment/repeated_names.py`): the name, every parent, and every proposal's full path. An O*NET round
 * can produce well over a hundred of them, so the round page shows each as a name with a parent count and
 * keeps the parents behind a disclosure. The text is the API's; this only splits it. A message that doesn't
 * match the expected shape keeps its original text, so a wording change upstream never hides a warning.
 */
import type { RepeatedNameWarning } from "@/lib/api/schemas";

export type RepeatedName = {
  level: "category" | "speciality";
  /** The name as the proposals spell it (`Adobe Acrobat`), not the API's matching key (`adobe acrobat`). */
  name: string;
  /** Each parent the name would go under, as `Service > Category`. */
  parents: string[];
  /** The API's own message, shown when it couldn't be split into parents. */
  message: string;
};

const CODE_PREFIX = /^warning\.repeated_(category|speciality)_name:\s*/;
const HEADLINE = /Repeated new (category|speciality) name (['"])(.*?)\2 across \d+ distinct parents\./;
const PARENT = /Parent (.+?): \[([^\]]*)\]/g;
const CATEGORY_ID = /\s*\(category ID \d+\)$/;

export function isRepeatedNameWarning(warning: string): boolean {
  return CODE_PREFIX.test(warning);
}

/** Split one warning message into its name and parents; `null` when it isn't a repeated-name warning. */
export function parseRepeatedName(message: string): RepeatedName | null {
  const text = message.replace(CODE_PREFIX, "");
  const headline = HEADLINE.exec(text);
  if (!headline) return null;
  const parents: string[] = [];
  let spelled: string | undefined;
  for (const [, label, members] of text.matchAll(PARENT)) {
    parents.push(label.replace(CATEGORY_ID, ""));
    // A member is "<proposal id>: Service > Category > Name"; its last part is the name as written.
    spelled ??= members.split("; ")[0]?.split(" > ").at(-1)?.trim() || undefined;
  }
  return { level: headline[1] as RepeatedName["level"], name: spelled ?? headline[3], parents, message: text };
}

/** The review queue's structured warning, with its parents read from the message. */
export function fromScreeningWarning(warning: RepeatedNameWarning): RepeatedName {
  const level = warning.code === "warning.repeated_category_name" ? "category" : "speciality";
  return parseRepeatedName(warning.message) ?? { level, name: warning.name, parents: [], message: warning.message };
}

/** A round's warnings with the repeated-name ones pulled out, most widely repeated first. */
export function splitRoundWarnings(warnings: string[]): { repeated: RepeatedName[]; other: string[] } {
  const repeated: RepeatedName[] = [];
  const other: string[] = [];
  for (const warning of warnings) {
    const parsed = isRepeatedNameWarning(warning) ? parseRepeatedName(warning) : null;
    if (parsed) repeated.push(parsed);
    else other.push(warning);
  }
  return { repeated: sortByReach(repeated), other };
}

export function sortByReach(names: RepeatedName[]): RepeatedName[] {
  return [...names].sort((a, b) => b.parents.length - a.parents.length || a.name.localeCompare(b.name));
}
