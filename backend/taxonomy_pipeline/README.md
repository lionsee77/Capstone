# Taxonomy validation pipeline

Scores taxonomy quality (integrity, breadth, depth, saturation) so Ankit's
original taxonomy and the team's SkillsFuture-generated one can be compared
on the same basis. Runs as a config-driven CLI, and as a FastAPI service so
a future admin dashboard (or the client, if scope changes again) can drive
it over HTTP.

It also hosts the classification workflow that places the new tags a
saturation run finds into the existing taxonomy (Case A and Case B) and
merges both results into one reviewable preview - see
[Classification workflow](#classification-workflow).

The CLI and the API call the exact same `pipeline.run_pipeline` - there is
one implementation of "what a run does," not two. Every CLI command is a thin
wrapper around one library function in the same way.

## Project structure

```text
taxonomy_pipeline/
├── configs/                          # example pipeline configs
├── docs/                             # design notes and future-work references
│   ├── saturation-coverage-options.md      #   options for deciding "good coverage" from the saturation curve
│   └── saturation-threshold-calibration.md #   method for choosing saturation thresholds from labelled pairs
├── src/taxonomy_pipeline/
│   ├── datasets/                     # pluggable taxonomy and reference sources
│   │   ├── __init__.py               #   source registry - register new `type:` values here
│   │   ├── base.py                   #   TaxonomySource protocol, canonical TaxonomyDataset
│   │   ├── sql_dump.py               #   reads plain .sql dump files
│   │   ├── ssic.py                   #   SSIC 2025 industry classification workbook
│   │   └── ssoc.py                   #   SSOC 2024 occupation classification workbook
│   ├── metrics/                      # pluggable quality checks
│   │   ├── base.py                   #   Metric ABC
│   │   ├── integrity.py              #   duplicates, orphans, dead ends
│   │   ├── depth.py                  #   branching factor
│   │   ├── breadth.py                #   coverage vs. a reference dataset
│   │   ├── saturation.py             #   new-items curve across ordered incoming sources
│   │   ├── category_adaptation.py    #   Case A: place a new source's own categories/specialities (see below)
│   │   └── registry.py               #   METRICS dict - register new metrics here
│   ├── classification/               # Case A/B producers and the merge stages (see "Classification workflow")
│   │   ├── models.py                 #   ClassificationOutput contract + every stage's report model
│   │   ├── routing.py                #   splits one saved saturation report into Case A / Case B tags
│   │   ├── case_a.py                 #   Case A: category_adaptation -> contract records
│   │   ├── from_category_adaptation.py #  serializes category_adaptation's placements into the contract
│   │   ├── case_b.py                 #   Case B: LLM placement for tags with no source category
│   │   ├── validation.py             #   merge 1: combine producer files, check paths against the baseline
│   │   ├── deduplication.py          #   merge 2: group hierarchy paths, suggest duplicate tag pairs
│   │   ├── resolution.py             #   merge 3: apply review decisions, assign temporary IDs
│   │   ├── preview.py                #   merge 4: assemble and structurally check the combined taxonomy
│   │   ├── export.py                 #   merge 5: preview -> CSV
│   │   └── runner.py                 #   chains merge 1-5 for `merge-preview`
│   ├── config.py                     # YAML/JSON pipeline config (datasets + metrics)
│   ├── models.py                     # MetricResult / RunReport - one report shape everywhere
│   ├── pipeline.py                   # run_pipeline(config) -> RunReport
│   ├── db.py                         # SQLite run history
│   ├── api/app.py                    # FastAPI: /metrics, /runs
│   └── cli.py                        # `taxonomy-pipeline run|serve|case-a|case-b|merge-preview`
├── schemas/
│   └── classification_output.schema.json # JSON Schema for the producer contract
└── tests/
    └── fixtures/                     # tiny synthetic SQL dumps used by the tests
        └── classification/           #   synthetic producer outputs, taxonomy and review decisions
```

## Classification workflow

Places the new tags saturation finds into the baseline taxonomy and builds one
reviewable merge preview. Nothing here writes to a database.

```text
taxonomy-pipeline run --output report.json     # saturation, ONCE per round
        │  ADD_CANDIDATE tags, split by parent_status (classification/routing.py)
        ├── HAS_PARENT ─► taxonomy-pipeline case-a ─► case_a_output.json
        └── NO_PARENT ──► taxonomy-pipeline case-b ─► case_b_output.json
                                      │
                 taxonomy-pipeline merge-preview ─► merge_runs/<timestamp>/
                 validate → deduplicate → resolve → preview → preview.csv
                                      │
                 reviewer writes decisions.json, reruns merge-preview --decisions
```

Run from `backend/taxonomy_pipeline/` (the commands default to
`configs/original_vs_skillsfuture.yaml`, `--baseline original` and
`--incoming skillsfuture`):

```bash
taxonomy-pipeline run --config configs/original_vs_skillsfuture.yaml --output report.json
taxonomy-pipeline case-a --report report.json
taxonomy-pipeline case-b --report report.json        # needs .[llm] and OPENAI_API_KEY
taxonomy-pipeline merge-preview --input case_a_output.json --input case_b_output.json
# review, then:
taxonomy-pipeline merge-preview --input case_a_output.json --input case_b_output.json \
    --decisions decisions.json
```

**Run saturation once per round and give both producers the same
`report.json`.** Case A takes the report's `ADD_CANDIDATE` + `HAS_PARENT` tags,
Case B its `ADD_CANDIDATE` + `NO_PARENT` tags, so together they cover every new
tag exactly once. Both fail if the report is missing or has no saturation
result for `--incoming`. Each record they write carries the report's `run_id`
as `saturation_run_id`, and `merge-preview` refuses to combine files from
different runs (unstamped files only produce a warning). `report.json` and the
stage outputs are git-ignored: regenerate them rather than committing them,
since they go stale whenever the data, thresholds or code change.

### Producer contract

Case A and Case B both write a JSON array in the shared contract: exactly nine
fields - `source_dataset`, `tag_id`, `tag`, `original_classification`,
`service`, `category`, `speciality`, `confidence` and `reason`. Unresolved
placements use explicit `null` for service, category, speciality and
confidence. Extra fields (Case B's `action`, the `saturation_run_id` stamp) are
allowed and ignored by the merge stages. Parse an array with
`CLASSIFICATION_OUTPUTS.validate_json(payload)` from
`taxonomy_pipeline.classification.models`; the published schema is
`schemas/classification_output.schema.json`. Field definitions, the merge
stages' rules and the review-decision format are in the
[classification output contract](CLASSIFICATION_OUTPUT_CONTRACT.md).

### Case B (LLM classification)

`case-b` asks an OpenAI model to place each parentless new tag into the
baseline's existing categories and specialities, or to propose a new one,
using the SkillsFuture unique-skills workbook (`--unique`, defaults to the one
in `backend/skillsfuture/`) for skill descriptions. Both taxonomies come from
the pipeline config, the same datasets saturation and Case A use. Install the
optional dependencies and set the key in `backend/.env` (copy
`backend/.env.example`):

```bash
pip install -e ".[dev,llm]"
```

```env
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
```

Options: `--batch-size` (default 5), `--model`, `--debug-response`,
`--checkpoint`, `--output` (default `case_b_output.json`; a CSV is written next
to it). Completed batches are checkpointed to a hidden file next to the output,
so an interrupted run resumes without paying for finished batches; a
successful run removes it. Each record also carries an `action`:
`assign_existing`, `propose_new_speciality` or `propose_new_category`.

### Merge preview

`merge-preview` chains the five merge stages and writes each one's report to
`--out-dir` (default `merge_runs/<timestamp>/`): `validation.json`,
`deduplication.json`, `resolution.json` and `preview.csv`.

- Tags with no duplicate candidates are accepted with their proposed
  placements; tags named in a candidate pair stay `pending` until a decision
  covers them (see [Resolve reviewed tags and links](CLASSIFICATION_OUTPUT_CONTRACT.md#resolve-reviewed-tags-and-links)
  for the `ReviewDecision` format, and
  `tests/fixtures/classification/review_decisions.json` for an example).
- `--semantic-model all-MiniLM-L6-v2` adds meaning-based duplicate candidates
  (needs `.[semantic]`); without it, only name/spelling candidates are found and
  the preview is marked partial.
- Older exports go through temporary adapters: `--legacy-case-a FILE`,
  `--legacy-case-b FILE=SOURCE_DATASET`, and `--skip-case-a-handoffs`.
- Exit code is non-zero if an input file is unreadable or the preview is
  blocked because the merge would introduce structural errors.

## Setup

```bash
cd backend/taxonomy_pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -e .` makes the package importable and gives you the
`taxonomy-pipeline` console command; `[dev]` additionally pulls in `pytest`,
`httpx` (for the FastAPI test client) and `ruff`. Running Case B also needs
the `[llm]` extra (`openai`, `python-dotenv`).

The supplied SkillsFuture comparison also enables optional semantic matching.
Install it once with `pip install -e ".[dev,semantic]"`. The first semantic
run downloads the configured model (`all-MiniLM-L6-v2`). If you only need the
fast exact/lexical checks, remove `semantic_model` from the saturation section
of the config.

## Run it against the two real datasets already in this repo

```bash
taxonomy-pipeline run --config configs/original_vs_skillsfuture.yaml
```

This loads `backend/sql-files-nus/*.sql` (Ankit's original taxonomy) and
`backend/skillsfuture/skillsfuture_taxonomy.sql` (the generated SkillsFuture
taxonomy's `sf_*` tables), runs integrity + depth on each, evaluates both
taxonomies against SSIC 2025 and SSOC 2024 for breadth, and compares
SkillsFuture concepts against the original taxonomy for `saturation` (see
[below](#saturation-new-items-curve)). Prints the full JSON report to stdout
and a one-line-per-metric summary to stderr; pass
`--output report.json` to write the report to a file instead (git-ignored;
about 22 MB for this comparison, because every saturation entry lists its
parents). Every run is also saved to SQLite run history; pass `--no-save` to
skip that.

## Saturation: new-items curve

`saturation` measures how many genuinely new concepts each incoming source
adds. List sources in order with `incoming: [skillsfuture, another, ...]`
(a single name still works). Source *k* is compared against the baseline
plus every concept from sources 1..*k*-1, so a falling rate across
`details.curve` is the evidence the taxonomy is approaching saturation. The
whole curve comes from one run; each run is already stored in SQLite.

Named rows are grouped per level by a match key (normalised name, `&` ->
`and`, `-isation` -> `-ization`, plural `s` dropped), so "Machine Learning"
under two parents is one concept.

Every incoming concept gets **one verdict**. First: is there a similar
concept at **any** level of what is known? Sources don't use levels the same
way (original specialities are skills; SkillsFuture specialities are job
titles and its tags are skills), so all levels are searched. Evidence tiers
run strongest first - same match key, close spelling (RapidFuzz >=
`threshold`) confirmed by the sentence model (>= `semantic_threshold`), then
meaning alone - and within a tier a same-level match wins. Every match
reports `baseline_level` and `match_type`.

| Incoming concept | Similar concept known? | Parents similar? | Verdict |
|---|---|---|---|
| no parent in its source, or a tag | yes | not checked | `SKIP_DUPLICATE` / `SKIP_SEMANTIC_DUPLICATE` |
| has a parent | yes, but at another level | not comparable | `REVIEW_OTHER_LEVEL` (`reason: parent_not_comparable`) |
| has a parent | yes | yes | `SKIP_DUPLICATE` / `SKIP_SEMANTIC_DUPLICATE` (`parent_check: same_parent`) |
| has a parent | yes | no | `REVIEW_NEW_PLACEMENT` |
| any | no - nothing even moderately close at any level | - | `ADD_CANDIDATE` |
| any | no - but a close spelling without meaning agreement, or partly similar | - | `REVIEW_AMBIGUOUS` |

Parent check details:

- Parents are resolved inside each concept's own source (speciality ->
  category, category -> service). An incoming parent is similar to a known
  parent with the same match key or, with the model, semantic score >=
  `semantic_threshold`. **Every** incoming parent must be similar to some
  parent of the known concept; `new_placement_details` lists only the
  `unmatched_incoming_parents`, next to `existing_parents`.
- The check only runs for same-level matches. A match at another level has
  a different kind of parent (e.g. a SkillsFuture sector vs the specialities
  an original tag hangs off), so it is `REVIEW_OTHER_LEVEL`: already known,
  so not new and not a new placement, but the team checks it.
- Tags skip the check: in this schema a tag is a shared keyword, so linking
  a known tag to new specialities is an integration step, not a new concept.
- Placements from earlier sources are remembered, so a later source is not
  flagged again for the same concept under the same parent.
- `REVIEW_NEW_PLACEMENT` is a known concept in a new context (e.g. "Machine
  Learning" under Finance when the taxonomy has it under Healthcare). It is
  **not** counted in the new-items rate; it has its own `new_placements` and
  `new_placement_rate` (new placements / placements checked).

Blank and placeholder names ("test", "new", ...) are listed in
`skipped_invalid`. A name that appears at two levels of the same source is
counted once, at the first level; the repeat is listed in
`repeated_in_source_other_level`. Set `cross_level: false` for a strict
level-by-level comparison.

`semantic_threshold_by_level` overrides `semantic_threshold` for matching
concepts of one incoming level, e.g. `{specialities: 0.80}`: SkillsFuture
job titles scored 0.70-0.75 against loosely related skills ("Head of Sales"
-> "sales"). The parent check always uses `semantic_threshold`.

Per step, `new_rate_min` = add candidates / concepts reviewed and
`new_rate_max` = (add candidates + review items) / concepts reviewed.

Every incoming concept - duplicates included - carries `parent_status`
(`HAS_PARENT` / `NO_PARENT`), `parent_level`, `parent_count` and `parents`,
resolved **inside its own source**: speciality -> category, category ->
service, tag -> linked specialities. The SkillsFuture dump defines no
services of its own, so its sectors (categories) are `NO_PARENT`.
`parent_status_counts` covers every reviewed concept;
`parent_status_counts_flagged` covers only `ADD_CANDIDATE`,
`REVIEW_AMBIGUOUS`, `REVIEW_NEW_PLACEMENT` and `REVIEW_OTHER_LEVEL`. For a duplicate skill, the
`parents` list shows which job roles link to a tag we already have.

`details.overall` and `details.by_concept_level` keep their existing shape
and describe the last source; `details.steps` holds the same block for every
source and `details.curve` lists `{step, source, reviewed, add_candidates,
review_ambiguous, new_rate_min, new_rate_max, new_placements,
new_placement_rate, review_other_level, cumulative_known_concepts}`.
Matches also report `matched_source` and `baseline_status`; matches to
inactive (`status='0'`) rows are counted in `matched_inactive`.

### Saturation settings

Defaults come from `SaturationMetric.default_params`
(`src/taxonomy_pipeline/metrics/saturation.py`); the last column is what
`configs/original_vs_skillsfuture.yaml` uses.

| Param | Default | Meaning | SkillsFuture config |
|---|---|---|---|
| `baseline` | - | Existing taxonomy dataset name | `original` |
| `incoming` | - | One dataset name or an ordered list; each is one point on the curve | `[skillsfuture]` |
| `threshold` | 90 | RapidFuzz score (0-100) for a "close spelling"; never enough on its own | 90 |
| `semantic_model` | none | Optional sentence-transformers model; without it only exact/spelling evidence is used | `all-MiniLM-L6-v2` |
| `semantic_threshold` | 0.70 | Cosine similarity for a meaning match; also used by the parent check | 0.70 |
| `semantic_threshold_by_level` | `{}` | Per incoming level override of `semantic_threshold` for concept matching | `{specialities: 0.80}` |
| `add_candidate_lexical_threshold` | 60 | `ADD_CANDIDATE` needs the best spelling score at every level below this | 60 |
| `add_candidate_semantic_threshold` | 0.50 | ...and (with a model) the best meaning score below this; otherwise `REVIEW_AMBIGUOUS` | 0.50 |
| `cross_level` | true | Also search the other levels of the known taxonomy | true |

### Reading the saturation report

For the integration team: everything is under
`results[metric_id == "saturation"].details.by_concept_level.<categories|specialities|tags>`
(the last incoming source; `details.steps[i]` has the same block per source).
Each incoming concept appears in exactly one list:

| Verdict (`recommended_action`) | List | What to do |
|---|---|---|
| `SKIP_DUPLICATE` | `exact_match_details`, `fuzzy_match_details` | Nothing - already in the taxonomy |
| `SKIP_SEMANTIC_DUPLICATE` | `semantic_match_details` | Nothing (spot-check meaning matches if in doubt) |
| `REVIEW_NEW_PLACEMENT` | `new_placement_details` | Known concept, new parent: decide whether to add it under `unmatched_incoming_parents` |
| `REVIEW_OTHER_LEVEL` | `other_level_match_details` | Similar concept at another level: confirm it really is the same concept |
| `ADD_CANDIDATE` | `ambiguous_concepts` | New: decide where to place it, using `parents` |
| `REVIEW_AMBIGUOUS` | `ambiguous_concepts` | Partly similar: check against `closest_baseline_name` / `closest_semantic_baseline_name` |

`reason` values: `no_close_match` (ADD), `partial_similarity` and
`close_spelling_unconfirmed` (REVIEW_AMBIGUOUS), `parent_differs` and
`existing_has_no_parent` (NEW_PLACEMENT), `parent_not_comparable`
(OTHER_LEVEL). Skipped duplicates carry `parent_check`: `same_parent`,
`no_incoming_parent` or `not_checked_for_tags`.

Useful fields on every entry: `incoming_ids` (all source rows grouped into
the concept), `incoming_name`, `parent_status` / `parents` (from the incoming
source), and for matches `baseline_id` / `baseline_name` / `baseline_level`
plus `matched_source` (which dataset the match came from).

Example - export every new skill with its SkillsFuture job roles to CSV:

```python
import csv
import json

with open("report.json") as f:
    report = json.load(f)
saturation = next(r for r in report["results"] if r["metric_id"] == "saturation")["details"]
skills = saturation["by_concept_level"]["tags"]["ambiguous_concepts"]

with open("add_candidate_skills.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["incoming_ids", "name", "parent_status", "parents"])
    for concept in skills:
        if concept["recommended_action"] == "ADD_CANDIDATE":
            writer.writerow([
                " ".join(str(i) for i in concept["incoming_ids"]),
                concept["incoming_name"],
                concept["parent_status"],
                "; ".join(parent["name"] for parent in concept["parents"]),
            ])
```

Swap `tags` for `specialities` / `categories`, or the list and verdict, to
export other groups.

## Category adaptation (Case A)

`category_adaptation` is the next step after saturation for tags a saturation
run already flagged as new (`ADD_CANDIDATE`) **and** that have a resolvable
source category of their own (saturation's own `parent_status ==
HAS_PARENT` on that tag - tags with `NO_PARENT` have nothing of their own to
place and are Case B's job instead, not this metric's).

It adapts the incoming source's own category/speciality structure onto the
baseline, one level at a time, reusing saturation's exact/lexical/semantic
matcher rather than reimplementing it:

1. **Category, one level up**: each new tag's source category is matched
   against every baseline category. A confident match reuses that baseline
   category's real ID; no confident match proposes a `NEW_CATEGORY`.
2. **Speciality, one level down, scoped to that category**: the same
   matcher runs again, but only against the baseline specialities that sit
   under whichever category just resolved (never the whole baseline
   speciality table). A `NEW_CATEGORY` has nothing under it yet, so its
   specialities all cascade to `NEW_SPECIALITY` automatically.

A category match that needs review (`REVIEW_AMBIGUOUS`, `REVIEW_NEW_PLACEMENT`,
`REVIEW_OTHER_LEVEL`, or an invalid name) is never auto-resolved and its
speciality is never evaluated either - resolving a speciality under an
unconfirmed category would look like a confident placement when the
foundation under it isn't confirmed.

### Params

Defaults come from `CategoryAdaptationMetric.default_params`
(`src/taxonomy_pipeline/metrics/category_adaptation.py`).

| Param | Default | Meaning |
|---|---|---|
| `baseline` | - | Existing taxonomy dataset name |
| `incoming` | - | The one source dataset name being adapted |
| `new_tag_ids` | - | Required; the confirmed-new tag IDs to place (from saturation's `ADD_CANDIDATE` + `HAS_PARENT` tags) |
| `threshold` | 90 | RapidFuzz score (0-100) for a "close spelling"; never enough on its own |
| `semantic_model` | none | Optional sentence-transformers model; without it only exact/spelling evidence is used |
| `semantic_threshold` | 0.6 | Cosine similarity for a meaning match, used for both categories and specialities. Lower than saturation's tag default (0.70): categories are broader, less precisely-worded concepts, and real runs show correct matches scoring as low as 0.63-0.69 |
| `add_candidate_lexical_threshold` | 60 | Below this *and* the semantic threshold, nothing is even close - `NEW_CATEGORY`/`NEW_SPECIALITY` instead of `REVIEW_AMBIGUOUS` |
| `add_candidate_semantic_threshold` | 0.5 | See above |

### Actions

Every placement has a category-level `action` and, once the category
resolves, a `speciality_action`:

| Action | Level | Meaning |
|---|---|---|
| `SKIP_DUPLICATE` / `SKIP_SEMANTIC_DUPLICATE` | category, speciality | Matched an existing baseline node; reuse its real ID |
| `NEW_CATEGORY` | category | No existing category matches; propose a new one |
| `NEW_SPECIALITY` | speciality | No existing speciality under the resolved category matches (or the category itself is brand new); propose a new one |
| `REVIEW_AMBIGUOUS` | category, speciality | Partial similarity at best; both the closest lexical and closest semantic baseline names are surfaced for review |
| `REVIEW_NEW_PLACEMENT` | category, speciality | Matches a known concept, but under a different parent than the source gives it (`unmatched_incoming_parents` vs `existing_parents`) |
| `REVIEW_OTHER_LEVEL` | category | Matches a known concept at a different taxonomy level |
| `INVALID_CATEGORY_NAME` | category | Source category name is blank or a placeholder |
| `NO_SOURCE_CATEGORY` | tag | The tag has no source category/speciality in this dataset at all - Case A has nothing to place it against; route to Case B |

A resolved category (`SKIP_*` or `NEW_CATEGORY`) also carries `target_service_id`/
`target_service_name`: looked up directly from the baseline for a matched
category, or guessed by majority vote across whichever *other* source
categories matched in the same run for a brand new one (`None` if nothing
else in the run matched anything to vote from - the placement then stays
fully unresolved, since the classification contract requires service,
category, speciality and confidence to be all-present or all-null together).

### Running it end to end

`taxonomy-pipeline case-a` reads the tag IDs from a saved saturation report
(`ADD_CANDIDATE` + `HAS_PARENT`, see [Classification workflow](#classification-workflow)),
runs `category_adaptation` on them, serializes the placements via
`classification.from_category_adaptation.build_classification_outputs`, and
validates the result against the contract before writing it out
(`case_a_output.json` by default). `--semantic-model ''` runs it lexically only.

```bash
taxonomy-pipeline run --config configs/original_vs_skillsfuture.yaml --output report.json
taxonomy-pipeline case-a --report report.json --output case_a_output.json
```

## Industry and occupational breadth

The `breadth` metric returns separate estimated lexical coverage scores:
SSIC division titles against industry category names (`service_id=1`), and
SSOC minor-group titles against all named specialities. It retains the
existing normalisation and RapidFuzz best-name matching. These are estimates,
not verified semantic coverage: original specialities describe capabilities,
whereas SkillsFuture specialities typically describe occupations.

Declare references as datasets (paths here are relative to `configs/`):

```yaml
datasets:
  # Include your existing taxonomy datasets too.
  - name: ssic
    type: ssic
    paths: [../../reference_data/ssic/ssic2025-classification-structure.xlsx]
  - name: ssoc
    type: ssoc
    paths: [../../reference_data/ssoc/ssoc2024-classification-structure.xlsx]
metrics:
  - id: breadth
    params:
      target: original
      ssic_reference: ssic
      ssoc_reference: ssoc
      industry_level: division
      occupational_level: minor_group
      industry_service_id: 1
      threshold: 75
```

Each reference requires exactly one workbook path. The old `reference`
parameter is no longer accepted; replace it with `ssic_reference` and
`ssoc_reference`. Other metrics continue accepting taxonomy datasets only.

Results contain `details.target`, `details.industry_service_id`, and separate
`details.industry` / `details.occupational` objects. Each dimension reports
reference name, system/version, level, threshold, target-node count,
matched/total counts, coverage, matched code/title and target ID/name/score,
and unmatched code/title records. There is no combined score. Each reference
code counts once; several reference codes may match the same target node.
Empty target selections score zero; empty reference levels return null coverage.
Existing CLI, API, and SQLite report interfaces are unchanged; old saved reports
remain readable.

## Run it as a service

```bash
taxonomy-pipeline serve --reload
```

- `GET /metrics` - self-describing catalogue of what's runnable.
- `POST /runs` - body is a `PipelineConfig` (the same schema as the YAML
  configs, as JSON); dataset paths must be absolute paths on the server. It
  returns `{run_id, status: "queued"}` immediately and runs the pipeline in
  the background.
- `GET /runs/{run_id}` - poll for status, which moves `"queued"` ->
  `"running"` -> `"done"` or `"failed"`; once finished, the full report is
  in the response.
- `GET /runs` - recent run history.

Every run (CLI or API) is persisted to `taxonomy_pipeline.sqlite3` next to
this README, so a future dashboard has run history to list and diff without
re-running anything.

## Plugging in a taxonomy dataset

Datasets are declared in config, not hardcoded:

```yaml
datasets:
  - name: whatever-you-want-to-call-it
    type: sql_dump          # taxonomy source; ssic/ssoc load reference workbooks
    paths: [path/to/a.sql, path/to/b.sql]   # one table per file, or all in one - both work
    table_map:               # canonical concept -> actual table name; omit a
      categories: sf_categories  # concept to default it to its own name
```

A table counts as present if the dump has a `CREATE TABLE` or an `INSERT`
for it; a concept whose table is missing entirely is loaded as empty but
reported as absent (`TaxonomyDataset.has_table()`), so metrics can skip a
check instead of passing it on no data.

To add a new source type (a live MariaDB connection, a CSV export, ...):
implement `TaxonomySource.load()` (see `datasets/base.py`) and register a
builder for it in `datasets/__init__.py`. No other file changes.

## Plugging in a metric

Subclass `Metric` (see `metrics/base.py`), implement `run(datasets, params)
-> MetricResult`, and add one line to `metrics/registry.py`. It's then
runnable by `id` from any config and shows up in `GET /metrics`.

Two things worth reusing rather than reimplementing when you do:
- `Metric.resolve_params(params)` merges your `default_params` under the
  caller's `params` - always call it first so a config only needs to
  override what it actually cares about.
- `Metric.require_datasets(datasets, {"role": name, ...})` resolves one or
  more dataset names, returning resolved datasets or an error `MetricResult`
  for missing names or wrong types. Taxonomy types are the default; breadth
  supplies reference types through `expected_types` -
  every metric here uses it instead of hand-rolling the same lookup.

## Config and report reference

**Config** (`config.py`) - what the CLI reads from YAML and what `POST
/runs` accepts as JSON. YAML paths can be relative to the YAML file; API
paths must be absolute server-side paths:

```yaml
datasets:
  - name: original          # referenced by name from metric params
    type: sql_dump
    paths: [...]
    table_map: {...}        # optional, see "Plugging in a taxonomy dataset"
metrics:
  - id: integrity            # must match a registered Metric.id
    params: {...}            # merged over that metric's default_params
```

**Report** (`models.py`) - what every run produces, whether you got it from
the CLI's stdout, `GET /runs/{run_id}`, or the SQLite `report_json` column:

```jsonc
{
  "run_id": "...",
  "status": "done",          // "queued" | "running" | "done" | "failed"
  "error": null,              // set only when status == "failed" (e.g. a dataset failed to load)
  "datasets": [{"name": "original", "counts": {"categories": 110, ...}}],
  "results": [
    {
      "metric_id": "integrity",
      "status": "ok",         // "ok" | "error" (bad params/dataset)
      "summary": "...",        // one-line, human-readable
      "details": {...}         // metric-specific structured data - see that metric's module for its shape
    }
  ]
}
```

A check inside a metric that can't run on a given dataset is reported as
`null`, not as an empty "all clean" result, and named with a reason in that
result's `details.skipped_checks` - e.g. integrity's
`orphans.categories_without_service` is `null` for a dataset that has no
`services` table (like `skillsfuture`). A `services` table that exists but
is empty is still checked, so every category pointing at a service is
reported as an orphan.

A metric erroring (bad params, unknown dataset) does **not** fail the whole
run - `status` stays `"done"` with that one result marked `"error"`; only a
dataset that fails to *load* fails the whole run, since nothing downstream
can run without it. `taxonomy-pipeline run` reflects this in its exit code:
non-zero if the run failed *or* any individual metric errored.

## Development

```bash
python -m pytest         # run the whole suite
python -m pytest -k sql_dump   # just one area
```

Tests import `taxonomy_pipeline` straight from `src/` via `pyproject.toml`'s
`tool.pytest.ini_options.pythonpath` - no editable install required to run
them, though `pip install -e ".[dev]"` gives you the `taxonomy-pipeline`
command too. `tests/fixtures/` holds tiny synthetic SQL dumps (a handful of
rows, deliberately including a duplicate/orphan/dead-end/unused-tag of each
kind) so metric tests don't depend on the real taxonomy data staying stable
shape-wise. `tests/conftest.py`'s `isolated_db` fixture points `db.py` at a
throwaway SQLite file per test - use it in any test that calls `db.save_run`
or hits the API, so runs don't leak into your real `taxonomy_pipeline.sqlite3`
or across tests.

Ruff linting and the test suite run in CI (see the root
`.github/workflows/ci.yml`).

## Known gaps

- **Case B service:** every resolved Case B placement is written with
  `service="Industry"`, although most baseline categories sit under Function,
  Subject or Others. A correct pick such as "Data analytics" (under Subject)
  therefore reaches the merge preview as a proposed *new* category under
  Industry. Tracked in #52; treat Case B rows in `preview.csv` accordingly.
- **No promotion step:** `merge-preview` stops at a reviewable CSV. Writing the
  merged taxonomy out, scoring it against the baseline and promoting it to the
  next baseline is the incremental flow in #49.

- **`saturation`** recommendations are review aids, not database
  operations. Its thresholds are not yet calibrated against a hand-labelled
  set of pairs (method in
  [`docs/saturation-threshold-calibration.md`](docs/saturation-threshold-calibration.md)),
  and with only SkillsFuture loaded the curve has one point.
  When a SkillsFuture skill matches both an original tag and an original
  speciality equally well, the tag is reported (same level wins ties), even
  though SF skills correspond more closely to original specialities. Most
  `REVIEW_OTHER_LEVEL` job roles are weak title -> keyword matches
  (e.g. "Technician" -> tag "tech"). There is no agreed "good coverage"
  cutoff for the curve yet; candidate methods are collected in
  [`docs/saturation-coverage-options.md`](docs/saturation-coverage-options.md).
- **`breadth`** and the fuzzy layer of **`integrity`** use lexical
  (RapidFuzz) matching only. Saturation additionally supports an optional
  `sentence-transformers` embedding model for semantic candidates.
- No live-database source yet - sources are `sql_dump`, `ssic`, and `ssoc`. `backend/audit/` already
  has a working live-DB connection (env vars `MYSQL_HOST` etc.) worth
  reusing when that source gets built, rather than reinventing it.
