/**
 * What a tag lookup result means for someone about to propose a term. No matching
 * happens here: which concepts match, and how closely, is entirely the API's.
 */
import type { ConceptLevel, LookupMatch, TagLookupResult, TaxonomyPath } from "@/lib/api/schemas";

/** Mirrors `app/lookup.py`'s limit, so an over-long term is stopped before a request. */
export const MAX_TERM_LENGTH = 200;

/** Whether the API would accept this term: at least one letter or digit, within the length limit. */
export function isLookupTerm(term: string): boolean {
  return term.length <= MAX_TERM_LENGTH && /[\p{L}\p{N}]/u.test(term);
}

export type LookupVerdict = { kind: "exists" | "close" | "new"; title: string; detail: string };

export function lookupVerdict(result: TagLookupResult): LookupVerdict {
  const where = `Checked against snapshot ${result.snapshot}.`;
  if (result.exists) {
    return {
      kind: "exists",
      title: "This term already exists",
      detail: `Reuse an existing concept instead of proposing it again. ${where}`,
    };
  }
  if (result.near.length > 0) {
    return {
      kind: "close",
      title: "No exact match, but some are close",
      detail: `Check whether one of these is what you mean before proposing it. ${where}`,
    };
  }
  return {
    kind: "new",
    title: "No match found",
    detail: `This looks new, though a longer name that contains it wouldn't show up here. ${where}`,
  };
}

/** How a concept matched, in words, with the score that made it a near match. */
export function matchLabel(match: LookupMatch): string {
  switch (match.match_type) {
    case "exact":
      return "Exact";
    case "variant":
      return "Spelling variant";
    case "lexical":
      return `Similar spelling (${match.lexical_score.toFixed(1)} / 100)`;
    case "semantic":
      return `Similar meaning (${(match.semantic_score ?? 0).toFixed(2)} / 1)`;
  }
}

/** A concept's parents as one line, leaving out the concept itself and any step an orphan row is missing. */
export function parentLabel(path: TaxonomyPath, level: ConceptLevel): string {
  const steps: [ConceptLevel, TaxonomyPath["service"]][] = [
    ["services", path.service],
    ["categories", path.category],
    ["specialities", path.speciality],
  ];
  return steps
    .filter(([stepLevel, ref]) => stepLevel !== level && ref !== null)
    .map(([, ref]) => ref?.name)
    .join(" › ");
}
