# Classification fixtures

Synthetic data for developing validation and merge previews without Lionel's reports.

| File | Scenario |
|---|---|
| `taxonomy.json` | Two categories, four specialities, and an existing SQL tag |
| `case_a.json` | Category-adapted tags, one tag with two placements, spelling overlap, and SQL reuse |
| `case_b.json` | AI placements, overlap within and across branches, and two distinct meanings of Python |
| `unresolved.json` | No proposed placement; reason retained |
| `invalid_placements.json` | Missing path and existing speciality under the wrong category |

Tag `42` appears in both datasets; its source identities must remain distinct.
The visualization tags are duplicate candidates for review. The Python tags should
stay separate. SQL may reuse baseline tag `200` and add a link to speciality `120`
after review.

Invalid placements satisfy the JSON contract; future taxonomy validation must reject
their paths. Only models and fixtures are implemented; matching and merging are pending.
