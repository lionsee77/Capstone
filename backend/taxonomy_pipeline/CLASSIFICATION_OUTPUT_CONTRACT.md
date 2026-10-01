# Classification output contract

**Producers:** Yi Jie (Case A), Zay Nin (Case B). **Consumer:** Bryan.

Return a JSON array with one object per proposed placement. Both cases use exactly
the same nine fields. Producers only need to generate JSON; Pydantic is used by
the consumer to validate it.

## Ankit's four taxonomy tiers

```text
Service -> Category -> Speciality -> Tag
```

| Tier | SQL table | Relationship | Output field |
|---|---|---|---|
| Service | [services](../sql-files-nus/services.sql) | Top level: Industry, Function, Subject, or Others | `service` |
| Category | [categories](../sql-files-nus/categories.sql) | `service_id` refers to `services.id` | `category` |
| Speciality | [specialities](../sql-files-nus/specialities.sql) | `category_id` refers to `categories.id` | `speciality` |
| Tag | [tags](../sql-files-nus/tags.sql) | Linked to specialities through `speciality_tags` | `tag` |

Example path from the SQL:

```text
Industry -> Banking -> Regulatory Reporting (Basel/IFRS9) -> ifrs9
```

[speciality_tags](../sql-files-nus/speciality_tags.sql) joins `speciality_id` and
`tag_id`; it is not a fifth tier. A tag can belong to multiple specialities.

Producers supply all four tiers explicitly. Categories and specialities may already
exist or be proposed additions. Reuse a category only within the supplied service,
and a speciality only within that category. Matching names under different parents
remain separate placements; existing nodes are not moved. Ambiguous reuse needs review.
The payload's `tag_id` is the source dataset's ID, not Ankit's `tags.id`.

## Fields

All nine fields are required. The revised contract permits no extra fields.

| Field | JSON type | Meaning |
|---|---|---|
| `source_dataset` | string | Source dataset name |
| `tag_id` | string | Original tag ID, e.g. `"42"` |
| `tag` | string | Unchanged tag text from the source dataset; this is the tag to insert into the database |
| `original_classification` | string | `"classified"` for Case A; `"unclassified"` for Case B |
| `service` | string or null | Proposed Ankit service: `"Industry"`, `"Function"`, `"Subject"`, or `"Others"` |
| `category` | string or null | Proposed Ankit category name |
| `speciality` | string or null | Proposed Ankit speciality name within that category |
| `confidence` | number or null | Confidence in this placement, from `0` to `1` inclusive |
| `reason` | string | Short placement explanation, or why no placement was found |

`original_classification` describes the tag **in its source dataset**, before Case
A/B processing. `tag` preserves the source text and supplies the database tag name.
Service, category, and speciality describe its **proposed Ankit placement**.

## Examples

Names below are illustrative. Producers may reuse Ankit's paths or propose new ones.

```json
[
  {
    "source_dataset": "categorized-skills",
    "tag_id": "42",
    "tag": "Data visualization",
    "original_classification": "classified",
    "service": "Subject",
    "category": "Data analytics",
    "speciality": "Data presentation",
    "confidence": 0.95,
    "reason": "The supplied category maps to data presentation."
  },
  {
    "source_dataset": "plain-skills",
    "tag_id": "42",
    "tag": "Data visualization",
    "original_classification": "unclassified",
    "service": "Subject",
    "category": "Data analytics",
    "speciality": "Business communication",
    "confidence": 0.88,
    "reason": "Visualizing data helps communicate findings."
  },
  {
    "source_dataset": "plain-skills",
    "tag_id": "48",
    "tag": "General practice",
    "original_classification": "unclassified",
    "service": null,
    "category": null,
    "speciality": null,
    "confidence": null,
    "reason": "Insufficient context to select a speciality."
  }
]
```

## Rules

- Identify tags by `(source_dataset, tag_id)`; IDs can repeat across datasets.
- For multiple placements, return separate objects with the same source identity,
  tag text, and original classification.
- Service, category, speciality, and confidence must either all have values or all be `null`.
- Keep `tag` unchanged and nonblank, including when its placement is unresolved.
- Strings must contain non-whitespace text. Use numeric confidence (`0.88`), not a
  quoted number (`"0.88"`), percentage, or boolean.
- Confidence helps review; it does not approve a placement or confirm duplicate tags.
- The taxonomy is supplied separately. No comparison results, version fields, or
  internal review/preview records belong in this payload.

## Implementation reference

The model, JSON Schema, and fixtures implement this nine-field contract. Batch
validation checks taxonomy paths and supports explicit temporary adapters for older
exports. Deduplication groups hierarchy paths and suggests overlapping tag pairs.
Resolution accepts unflagged classifications and applies explicit duplicate decisions. Preview construction validates
the combined snapshot and exports a CSV for review.

- [JSON Schema](schemas/classification_output.schema.json)
- [Python validator](src/taxonomy_pipeline/classification/models.py): `CLASSIFICATION_OUTPUTS.validate_json(payload)`
- [Case A fixture](tests/fixtures/classification/case_a.json)
- [Case B fixture](tests/fixtures/classification/case_b.json)
- [All fixture scenarios](tests/fixtures/classification/README.md)

## Temporary intake for current exports

Use [validate_outputs](src/taxonomy_pipeline/classification/validation.py) to combine
named JSON batches. Without adapter options, inputs must follow the nine-field contract.

- `case_a_inputs`: legacy eight-field files; maps `original_tag` to `tag`.
- `case_b_sources`: legacy seven-field files mapped to their source dataset names;
  sets `original_classification` to `unclassified` and keeps `action` in raw evidence.
- Both adapters temporarily set `service` to `Industry` for populated placements,
  recorded as a `temporary_service` finding. Unresolved placements remain all-null.
  Current nine-field inputs retain their explicit service; this default only applies
  to legacy exports until producers supply the field.
- Missing categories/specialities are valid proposed additions. The supplied service
  must exist. Ambiguous existing nodes and malformed matching taxonomy rows need review.
- Case B actions remain raw evidence. `assign_existing`, `propose_new_category`, and
  `propose_new_speciality` all pass through the same reuse/addition checks.
- `skip_case_a_handoffs=True`: temporarily skips legacy Case A rows with all-null
  placements and the explicit phrase `route to Case B` in their reason. Skips remain
  in the report and do not participate in cross-branch conflict checks. Default: false.

Run from `backend/taxonomy_pipeline/`:

```python
import json
from pathlib import Path

from taxonomy_pipeline.classification.validation import validate_outputs
from taxonomy_pipeline.datasets.sql_dump import SqlDumpSource

backend = Path("..")
taxonomy = SqlDumpSource("ankit-original", sorted((backend / "sql-files-nus").glob("*.sql"))).load()
inputs = {
    name: (backend / "reference_data" / name).read_text(encoding="utf-8")
    for name in ("case_a_output.json", "classification_proposals.json")
}
report = validate_outputs(
    inputs, taxonomy,
    case_a_inputs={"case_a_output.json"},
    case_b_sources={"classification_proposals.json": "skillsfuture"},
    skip_case_a_handoffs=True,
)
destination = Path("../../exports/classification-intake")
destination.mkdir(parents=True, exist_ok=True)
(destination / "validation.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
(destination / "merged_outputs.json").write_text(
    json.dumps([row.model_dump(mode="json") for row in report.merged_outputs], indent=2),
    encoding="utf-8",
)
```

`merged_outputs` contains only unique valid placements in the nine-field format.
Repeated placements for the same source identity and normalized full path are collapsed;
different source tags are never merged. Raw rows retain their individual scores and reasons.

The report includes every row's `origin` (file and zero-based index), `raw` data,
adapted `candidate`, validated `output` where available, resolved `target` IDs,
`status`, `findings`, and `duplicate_of` reference. Within a resolved target, null
`category_id` or `speciality_id` means that node is a proposed addition at the output's
named path. A new category always requires a new speciality under it. These null IDs
do not mean the classification is unresolved; its nine output fields remain populated.
`new_category` and `new_speciality` findings identify additions without triggering review.

Statuses are `valid`, `duplicate`, `unresolved`, `review`, `invalid`, and `skipped`.
Conflicting source text/classification flags and ambiguous reuse require review.
Unreadable files appear in `input_errors`; `complete` only means all files parsed as
arrays. A valid proposal is eligible for the merge preview; validation does not create
nodes or write to the database.

## Deduplication

Pass the intake report to `deduplicate_outputs`:

```python
from taxonomy_pipeline.classification.deduplication import deduplicate_outputs

deduplication = deduplicate_outputs(report)
(destination / "deduplication.json").write_text(
    deduplication.model_dump_json(indent=2), encoding="utf-8",
)
```

- `tags` groups eligible placements by `(source_dataset, tag_id)`, retaining source
  text, classification, scores, reasons, and original file/row references.
- `hierarchy` groups categories by service/name and specialities by the full parent
  path, using intake's normalization. Each group retains raw label variants and
  existing IDs; null IDs indicate new nodes. Names under different parents stay separate.
- `candidates` lists each distinct tag pair once, with source references, match types,
  separate lexical/semantic scores, and shared proposed paths. Tag evidence is in `tags`.
  No tag identities are merged automatically, even for identical names or high scores.
- `omitted_counts` accounts for skipped, unresolved, invalid, and review records;
  repeated unresolved records are also omitted. Valid repeated placements remain as evidence.

Name checks reuse saturation's `match_key`; lexical matching uses the same RapidFuzz
ratio. Defaults are `lexical_threshold=0.9` and `semantic_threshold=0.7`, both on 0..1.
All qualifying pairs are retained; similarity chains do not create approved groups.
The candidate's `similarity` is the largest available name score, not a probability
or approval; inspect the separate scores and placement context.

Semantic checks are optional at runtime. Supply an existing
`taxonomy_pipeline.metrics.saturation.SemanticMatcher` as `semantic_matcher` to use
the shared embedding implementation. Without a matcher, name/lexical checks run and
`skipped_checks.semantic` explains the omission. Matcher failures likewise retain
name-based results with an explicit finding. `complete` requires readable input files
and all applicable matching checks to run; it does not mean the candidate decisions
are resolved. No baseline tag comparison, database writes, or preview construction occur.

## Resolve reviewed tags and links

Use `resolve_outputs(deduplication, taxonomy, decisions)` from
`taxonomy_pipeline.classification.resolution`. This consumes the shared deduplication
report, independent of producer filenames, branch formats, and source dataset names.
The nine-field producer contract is unchanged.

Valid source tags with no duplicate candidates are accepted automatically, keeping
all supplied placements and creating any required category/speciality nodes under
their supplied parents. Tags named in duplicate candidate pairs remain pending until
reviewed. Skipped semantic checks remain visible and make the report partial; they
do not hold every otherwise eligible tag. Invalid/unresolved/skipped intake rows stay omitted.

Explicit decisions override this default, including exclusion, deferral, reuse, and
restricting approved paths. Automatically accepted tags get an explanatory source
disposition; `decisions` contains only supplied reviews, not fabricated human decisions.

`ReviewDecision` is an internal review format:

| Field | Meaning |
|---|---|
| `members` | Source references (`source_dataset`, `tag_id`) covered by one decision |
| `action` | `create`, `reuse`, `exclude`, or `defer` |
| `reason` | Required review explanation |
| `existing_tag_id` | Required only for `reuse`; must identify one baseline tag |
| `canonical_name` | For `create`: choose one member's unchanged source label when labels differ; inferred when all match |
| `approved_paths` | Explicit service/category/speciality paths, supporting existing and proposed nodes |
| `approved_speciality_ids` | Alternative approval by existing speciality ID |

Only placements proposed by the group's members can be approved. For an explicit
review decision, equivalence alone does not approve links: both approval lists default
to empty. Automatic acceptance includes all supplied paths for unflagged tags. `exclude` and `defer`
cannot approve links. Reuse preserves the baseline label.

```python
from taxonomy_pipeline.classification.models import ReviewDecision, TaxonomyPath
from taxonomy_pipeline.classification.resolution import resolve_outputs

# Example approval for a source identity present in the deduplication report.
decision = ReviewDecision(
    members=[{"source_dataset": "categorized-skills", "tag_id": "42"}],
    action="create",
    approved_paths=[TaxonomyPath(
        service="Subject", category="Data analytics", speciality="Data presentation",
    )],
    reason="Reviewed concept and placement.",
)
resolution = resolve_outputs(deduplication, taxonomy, [decision])
```

- One multi-member accepted decision confirms equivalence. Separate accepted decisions
  retain distinct tags, even for identical names. Disjoint `reuse` decisions targeting
  the same baseline tag consolidate into one record.
- Candidate participants without decisions remain `pending`; unflagged tags are
  accepted with their supplied paths. Excluded and deferred sources retain their
  evidence and reasons. Exact repeated decisions are collapsed; overlapping or
  contradictory decisions raise `ValueError` before a result is returned.
- New hierarchy IDs hash the normalized full parent path. New tag IDs hash sorted
  source identities. Separate `new:category:`, `new:speciality:`, and `new:tag:`
  namespaces keep them distinct from integer baseline IDs. Group membership changes
  produce a new tag ID; changing input order does not.
- `ResolutionReport` contains approved hierarchy nodes, canonical tags, mappings,
  unique links, source dispositions, counts, decisions, and the full deduplication
  evidence. Hierarchy nodes without approved links are omitted. Unapproved placements
  remain visible; repeated links are collapsed and baseline links marked `existing`.
- Path reuse is rechecked against the supplied baseline. Changed or ambiguous targets
  require revalidation. A content fingerprint identifies the baseline used for resolution.
  It does not prove that an older deduplication report used that exact snapshot.
- `partial` is true for incomplete upstream checks, omitted input records, pending or
  deferred sources, or accepted tags with unapproved placements. It is not merge approval.

See [synthetic review decisions](tests/fixtures/classification/review_decisions.json).
This stage returns proposed changes without modifying the baseline or writing to a database.

## Merge preview CSV

Build the preview from resolution and the same baseline, then export it:

```python
from taxonomy_pipeline.classification.preview import build_preview
from taxonomy_pipeline.classification.export import export_preview_csv

preview = build_preview(resolution, taxonomy)
export_preview_csv(preview, destination / "preview.csv")
```

`preview.csv` contains four row types. Filter `row_type=placement` for the review table.

| Row type | Contents |
|---|---|
| `placement` | Source dataset/ID/tag, original classification, four-tier path, confidence/reason, canonical tag/ID, category/speciality IDs, change labels, and review reason |
| `summary` | Baseline fingerprint, unique addition/reuse counts, source dispositions, and original input record counts |
| `omission` | Counts of records omitted upstream, grouped by status; individual records remain in `validation.json` |
| `check` | Baseline defects, introduced errors, skipped checks, and supporting metric failures |

Placement statuses are `proposed` (ready for addition in the preview), `reused`, `pending`, `deferred`, `excluded`,
`unapproved`, or `blocked`. Change columns mark approved nodes/links as `new` or
`existing`. Unflagged valid tags become `proposed` automatically; pending tags need
duplicate review. Explicit canonical mappings do not approve every source placement: an unapproved
placement remains unapproved even when another member links the same canonical tag there.

The CSV retains distinct confidence/reason variants for a source placement. It does
not repeat identical evidence rows or dump the entire baseline. CSV row counts are
therefore different from original input-record counts and unique tag/link counts.
`omitted_records` counts upstream omissions; pending/deferred/excluded sources and
unapproved placements have separate counters. Omitted source details cannot be recovered
from deduplication's aggregate counts alone; use the intake report for those details.

The in-memory `MergePreview.taxonomy` contains a copy of the baseline plus approved
additions in parent-first order. Tags without approved links are omitted. Existing
nodes and links are reused; same-name concepts remain separate when reviewed separately.
The builder verifies the baseline fingerprint and rechecks the resolution against its
recorded decisions. It checks IDs, parent/link endpoints, and duplicate link pairs
explicitly, with `IntegrityMetric` retained as supporting evidence.

Existing baseline defects are reported separately. Introduced errors block the whole
batch: the returned taxonomy contains only the baseline, addition counts are zero,
and affected review rows are marked `blocked`. A changed baseline requires rerunning
resolution. `validation.complete` means structural checks passed without introduced
errors; `partial` still records upstream omissions or incomplete review/checks.
Neither flag authorizes a database write.

UTF-8 CSV output supports Excel and standard CSV readers. Formula-like source text
is prefixed with an apostrophe in the CSV; model evidence remains unchanged.
Exporting to the same path replaces that file. CLI-managed run directories are still pending.
