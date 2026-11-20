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
const HEADLINE = /^Repeated new (category|speciality) name (['"])(.*?)\2 across (\d+) distinct parents\. /;
// Names may hold "]" or "; ", so groups and members are found by the proposal IDs (sha256 hex) that open them.
const PROPOSAL_ID = "[0-9a-f]{64}";
const GROUP = new RegExp(`(?:^|\\]; )Parent (.+?): \\[(?=${PROPOSAL_ID}: )`, "g");
const MEMBER = new RegExp(`; (?=${PROPOSAL_ID}: )`);
const MEMBER_ID = new RegExp(`^${PROPOSAL_ID}: `);
const TAIL = "]. Advisory only";
const CATEGORY_ID = /\s*\(category ID \d+\)$/;

export function isRepeatedNameWarning(warning: string): boolean {
  return CODE_PREFIX.test(warning);
}

/**
 * Split one warning message into its name and parents; `null` when it isn't a repeated-name warning.
 * When the parents found don't add up to the count the message states, they are dropped so the list
 * falls back to the message itself rather than showing a split it can't vouch for.
 */
export function parseRepeatedName(message: string): RepeatedName | null {
  const text = message.replace(CODE_PREFIX, "");
  const headline = HEADLINE.exec(text);
  if (!headline) return null;
  const [, level, , key, count] = headline;
  const fallback: RepeatedName = { level: level as RepeatedName["level"], name: key, parents: [], message: text };
  const body = text.slice(headline[0].length);
  const end = body.lastIndexOf(TAIL);
  if (end < 0) return fallback;
  const groups = [...body.slice(0, end).matchAll(GROUP)];
  if (groups.length === 0 || groups.length !== Number(count)) return fallback;
  // The first member reads "<proposal id>: Service > Category > Name"; keep as many trailing parts as the
  // name itself has, so a name containing " > " stays whole.
  const first = groups[0];
  const members = body.slice(first.index + first[0].length, groups[1]?.index ?? end);
  const path = members.split(MEMBER)[0].replace(MEMBER_ID, "");
  const spelled = path.split(" > ").slice(-key.split(" > ").length).join(" > ").trim();
  return { ...fallback, name: spelled || key, parents: groups.map((group) => group[1].replace(CATEGORY_ID, "")) };
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
