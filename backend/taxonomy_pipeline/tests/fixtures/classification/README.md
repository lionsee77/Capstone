# Classification fixtures

Synthetic data for developing validation and merge previews without Lionel's reports.

| File | Scenario |
|---|---|
| `taxonomy.json` | Subject, Industry, Function; two Subject categories, four specialities, and an existing SQL tag |
| `case_a.json` | Category-adapted tags, one tag with two placements, spelling overlap, and SQL reuse |
| `case_b.json` | AI placements, overlap within and across branches, and two distinct meanings of Python |
| `unresolved.json` | No proposed placement; reason retained |
| `invalid_placements.json` | Historical filename: paths absent from the baseline, now accepted as proposed additions |
| `legacy_case_a.json` | Legacy classified placement and an all-null Case B handoff |
| `legacy_case_b.json` | Legacy existing/new path proposals, without source dataset or service |
| `review_decisions.json` | Synthetic decisions merging visualization variants, reusing SQL, and keeping both Python meanings separate |

Tag `42` appears in both datasets; its source identities must remain distinct.
The visualization tags are duplicate candidates for review. The Python tags should
stay separate. SQL may reuse baseline tag `200` and add a link to speciality `120`
after review.

Missing category/speciality paths are valid proposed additions under the supplied
parents. Matching names under another parent do not reuse or move that node.
Legacy adapters temporarily assign Industry to populated placements; unresolved
placements stay all-null. Tests cover ambiguity, source conflicts, and handoff skips.
Deduplication tests use these fixtures for within/across-branch candidates, distinct
same-name concepts, multiple placements, and preserved source identities. Synthetic
matcher scores cover semantic-only candidates and similarity chains without downloads.
Resolution tests apply explicit review decisions, assign stable temporary IDs, and
collect approved links. Additional synthetic cases cover all four services, new paths,
conflicting decisions, baseline changes, and source IDs from arbitrary datasets.
Unflagged tags accept all supplied paths automatically; duplicate candidates stay
pending. Explicit review decisions override automatic acceptance.
The review fixture is for tests only; it does not approve the real reference exports.
Preview tests build the combined snapshot, validate structural changes, and export
CSV rows. They cover baseline defects, blocked additions, partial reviews, existing
links, reproducibility, Unicode/CSV quoting, and spreadsheet formula escaping.
