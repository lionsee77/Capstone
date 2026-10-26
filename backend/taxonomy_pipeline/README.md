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

The CLI and the API call the exact same `app.scoring.run_pipeline` - there is
one implementation of "what a run does," not two. Every CLI command is a thin
wrapper around one use case in `app/` in the same way: the use case does the
loading, checks and writes, and the CLI only parses flags and prints.

## Project structure

```text
taxonomy_pipeline/
├── baselines/                        # stored baseline-of-record reports, one per definitions version
│   └── baseline_v1.json.gz           #   the original taxonomy scored with definitions v1
├── configs/                          # pipeline configs
│   ├── baseline_v1.yaml              #   pinned config that produced baselines/baseline_v1.json.gz
│   └── original_vs_skillsfuture.yaml #   classification rounds: scores both taxonomies, saturation judge on
├── docs/                             # design notes and future-work references
│   ├── saturation-coverage-options.md      #   options for deciding "good coverage" from the saturation curve
│   └── saturation-threshold-calibration.md #   method for choosing saturation thresholds from labelled pairs
├── experiments/
│   ├── evaluate_case_b.py            # compare saved Case B outputs across prompt versions
│   └── golden_run.py                 # record every stage on the real data; diff two recordings (refactor check)
├── src/taxonomy_pipeline/            # laid out by bounded context; each imports only the ones below it
│   ├── taxonomy/                     # the taxonomy model and its pluggable sources
│   │   ├── __init__.py               #   source registry - register new `type:` values here
│   │   ├── base.py                   #   TaxonomySource protocol, canonical TaxonomyDataset, its Hierarchy (parent lookups)
│   │   ├── sql_dump.py               #   reads plain .sql dump files
│   │   └── references/               #   classification references a taxonomy is measured against
│   │       ├── workbook.py           #     the shared workbook loader and reference/record base models
│   │       ├── ssic.py               #     SSIC 2025 industry classification: levels and code rule
│   │       └── ssoc.py               #     SSOC 2024 occupation classification: levels and code rule
│   ├── matching/                     # anything that answers "is this the same concept?"
│   │   ├── names.py                  #   normalize, match_key, collation_key (which stage uses which), lexical scores
│   │   ├── semantic.py               #   SimilarityMatcher protocol + the sentence-model SemanticMatcher
│   │   ├── embeddings.py             #   pinned sentence-model loader (one revision pin for every matcher)
│   │   ├── preprocessing.py          #   breadth's label normalisation profiles
│   │   ├── judge.py                  #   optional LLM judge (OpenAI Decisions API) for saturation's ambiguous concepts
│   │   └── engine.py                 #   the comparison engine saturation and Case A share
│   ├── quality/                      # pluggable quality checks that score one taxonomy
│   │   ├── base.py                   #   Metric ABC
│   │   ├── integrity.py              #   duplicates, orphans, dead ends
│   │   ├── depth.py                  #   branching factor
│   │   ├── breadth.py                #   coverage vs. a reference dataset
│   │   ├── saturation.py             #   new-items curve across ordered incoming sources
│   │   ├── definitions.py            #   METRIC_DEFINITIONS_VERSION and check_comparable (see "Official metric definitions")
│   │   └── registry.py               #   METRICS dict - register new metrics here
│   ├── providers/                    # what is specific to one external source
│   │   ├── base.py                   #   SourceProvider ABC: per-tag context and display name for Case B
│   │   ├── registry.py               #   PROVIDERS by name; `case-b --provider`
│   │   └── skillsfuture.py           #   SkillsFuture: tag-ID offset, unique-skills workbook context
│   ├── enrichment/                   # Case A/B producers and the merge stages (see "Classification workflow")
│   │   ├── models.py                 #   ClassificationOutput contract + every stage's report model
│   │   ├── routing.py                #   splits one saved saturation report into Case A / Case B tags
│   │   ├── case_a.py                 #   Case A: category_adaptation -> contract records
│   │   ├── category_adaptation.py    #   Case A: place new tags by content, else via their source category (see below)
│   │   ├── from_category_adaptation.py #  serializes category_adaptation's placements into the contract
│   │   ├── case_b.py                 #   Case B: LLM placement for tags with no source category
│   │   ├── validation.py             #   merge 1: combine producer files, check paths against the baseline
│   │   ├── deduplication.py          #   merge 2: group hierarchy paths, suggest duplicate tag pairs
│   │   ├── resolution.py             #   merge 3: apply review decisions, assign temporary IDs
│   │   ├── review.py                 #   hierarchy decisions applied to screened proposals
│   │   ├── review_view.py            #   saved proposal review view behind `review-proposals` and /proposals
│   │   ├── change_log.py             #   append submitted decisions to the run's local audit log
│   │   ├── preview.py                #   merge 4: assemble and structurally check the combined taxonomy
│   │   ├── export.py                 #   merge 5: preview -> CSV
│   │   ├── runner.py                 #   chains merge 1-5 for `merge-preview`
│   │   ├── bulk_decisions.py         #   `bulk-decide`: draft decisions for ambiguous tags by similarity
│   │   ├── integrate.py              #   reviewed merge run -> enriched snapshot (real IDs, additions.sql)
│   │   └── proposals/                #   proposed categories and specialities, until governance (PB06)
│   │       ├── intake.py             #     collect proposed hierarchy paths
│   │       ├── screening.py          #     screen them against the baseline's categories and specialities
│   │       └── models.py             #     screening report shapes
│   ├── app/                          # use cases: what every CLI command and API route calls
│   │   ├── scoring.py                #   run_pipeline(config) -> RunReport; dataset/report I/O; check_runs; run history
│   │   ├── enrichment.py             #   case_a, case_b, merge_preview, review_proposals, bulk_decide, integrate
│   │   └── browse.py                 #   taxonomy search/detail over a past run's dataset (/taxonomy)
│   ├── config.py                     # YAML/JSON pipeline config (datasets + metrics + optional definitions pin)
│   ├── models.py                     # shared run reports, comparability checks, API response shapes
│   ├── db.py                         # SQLite run history
│   ├── env.py                        # loads backend/.env for `serve` and llm.py
│   ├── llm.py                        # the one OpenAI client setup (judge + Case B): key check, base URL
│   ├── api/
│   │   ├── app.py                    #   FastAPI: /metrics, /runs, /taxonomy routes over app/
│   │   ├── proposals.py              #   /proposals: which merge runs a request may read, over app.enrichment
│   │   └── security.py               #   API token and allowed data directories
│   └── cli.py                        # `taxonomy-pipeline run|check-comparable|serve|case-a|case-b|merge-preview|review-proposals|bulk-decide|integrate`
├── schemas/
│   └── classification_output.schema.json # JSON Schema for the producer contract
└── tests/
    └── fixtures/                     # tiny synthetic SQL dumps used by the tests
        └── classification/           #   synthetic producer outputs, taxonomy and review decisions
```

## Moved in the bounded-context refactor (#104)

The package was reorganised in two PRs: #110 moved files into the context
folders above, and #111 added the interfaces (`app/`, `llm.py`, `providers/`
adapters, the hierarchy helper). Pipeline outputs did not change - a golden run
(`experiments/golden_run.py`) on the code before #110 and after #111 matches on
every file, command output and Case B prompt, except the new `reason_codes`
field in `resolution.json`. Paths below are under `src/taxonomy_pipeline/`.

**Files**

| Before | After |
|---|---|
| `datasets/__init__.py`, `base.py`, `sql_dump.py` | `taxonomy/` (same names) |
| `datasets/ssic.py`, `ssoc.py` | `taxonomy/references/ssic.py`, `ssoc.py`, sharing the new `workbook.py` loader |
| `metrics/base.py`, `registry.py`, `integrity.py`, `depth.py`, `breadth.py` | `quality/` (same names) |
| `definitions.py` | `quality/definitions.py` |
| `metrics/saturation.py` | split: `matching/engine.py` (comparison engine), `matching/names.py` (name keys, lexical scores), `quality/saturation.py` (the metric) |
| `metrics/embeddings.py`, `judge.py`, `preprocessing.py` | `matching/` (same names) |
| `matching.py` | `matching/semantic.py` |
| `metrics/category_adaptation.py` | `enrichment/category_adaptation.py` (no longer a registered metric) |
| `classification/*.py` | `enrichment/*.py` (same names) |
| `proposals/` | `enrichment/proposals/` |
| `pipeline.py` | `app/scoring.py` |
| `api/taxonomy.py` | `app/browse.py` |

**Logic**

| What | Before | After |
|---|---|---|
| What a CLI command does (checks, file writes, warnings) | inside `cli.py` | `app/scoring.py`, `app/enrichment.py`; `cli.py` and `api/` only translate input/output |
| OpenAI client setup, placeholder-key check | `metrics/judge.py` and `classification/case_b.py` | `llm.py` |
| Loading `backend/.env` | three places | `env.py` only (via `llm.py` for the LLM) |
| Service/category/speciality id lookups | rebuilt in saturation, Case A, `/taxonomy`, Case B | `TaxonomyDataset.hierarchy()` in `taxonomy/base.py` |
| Name normalisation | `integrity.normalize`, `saturation.match_key`, `integrate._collation_key` | `matching/names.py` (`normalize`, `match_key`, `collation_key`; its docstring says which stage uses which) |
| SkillsFuture specifics (`TAG_ID_OFFSET`, unique-skills workbook) | `classification/case_b.py`, `cli.py` | `providers/skillsfuture.py`, behind `providers/base.py` + `registry.py` |
| `InputLocation`, `SourceTagRef`, `HierarchyGroup` | root `models.py` | `enrichment/models.py` |
| Classification placement path | `TaxonomyPath` in `classification/models.py` | `PlacementPath` in `enrichment/models.py` (the API's `TaxonomyPath` keeps its name) |
| Why a tag is pending | text prefix "Producer requested review" | `SourceDisposition.reason_codes` |
| Reading a merge run's `run.json` | parsed by hand in `cli.py` | `MergeRunInfo` + `enrichment.runner.load_merge_run()` |
| `GET /metrics`, `POST /runs`, `GET /runs` responses | plain dicts | `MetricInfo`, `RunCreated`, `RunSummary` in `models.py` |
| Case B progress output | `print` | logging (`taxonomy_pipeline.enrichment.case_b`); the CLI shows it |

**After pulling**, delete the old package folders git leaves behind (they hold only
`__pycache__`, but let Python import the old names as empty packages):
`rm -rf src/taxonomy_pipeline/{datasets,metrics,classification,proposals}`.

**Import paths** - there are no compatibility shims, so update imports:

```text
taxonomy_pipeline.datasets[.x]        -> taxonomy_pipeline.taxonomy[.x]  (ssic/ssoc: taxonomy.references.x)
taxonomy_pipeline.metrics.<metric>    -> taxonomy_pipeline.quality.<metric>
taxonomy_pipeline.metrics.<embeddings|judge|preprocessing> -> taxonomy_pipeline.matching.<same>
taxonomy_pipeline.matching            -> taxonomy_pipeline.matching.semantic
taxonomy_pipeline.definitions         -> taxonomy_pipeline.quality.definitions
taxonomy_pipeline.classification.x    -> taxonomy_pipeline.enrichment.x
taxonomy_pipeline.proposals.x         -> taxonomy_pipeline.enrichment.proposals.x
taxonomy_pipeline.pipeline            -> taxonomy_pipeline.app.scoring
```

**Removed**

- The legacy Case A/B intake adapters (`LegacyClassificationOutput`,
  `merge-preview --legacy-case-a/--legacy-case-b/--skip-case-a-handoffs`);
  old-format files are now reported as invalid rows.
- `backend/skillsfuture/validate_classification_proposals.py` -
  `experiments/evaluate_case_b.py` uses `enrichment/validation.py` instead.
- The `jsonschema` dependency, and `category_adaptation` from `GET /metrics`.

**Changed calls**

- `case-b` takes `--provider` (default: the `--incoming` dataset name) and
  `--context` (`--unique` still works); `run_case_b(provider=..., context=...)`
  replaces `unique_skills=...`.
- `experiments/evaluate_case_b.py` takes `--config` and `--baseline <dataset
  name>` instead of a SQL directory and `--skillsfuture-sql`.

## Classification workflow

Places the new tags saturation finds into the baseline taxonomy, builds one
reviewable merge preview, and turns the reviewed result into an enriched
taxonomy snapshot. Nothing here writes to a database.

> **Use `judged_report.json` for every classification round.** The SkillsFuture
> config runs saturation with the [Decisions API judge](#decisions-api-judge)
> on, which settles most ambiguous tags before they reach Case A/B (1,410 ->
> 177 on SkillsFuture). Write that report as `judged_report.json` and pass it
> explicitly: `case-a`, `case-b` and `bulk-decide` still default to
> `--report report.json` (an unjudged report), so always give `--report`.
> Case A, Case B and bulk-decide must all read the **same** report - each run
> has its own `run_id` and `merge-preview` refuses to mix them. Running the
> judge needs `OPENAI_API_KEY` in `backend/.env` and `pip install -e ".[llm]"`;
> without a key, comment out the `judge:` block, write `report.json` instead,
> and use it for the whole round.

```text
taxonomy-pipeline run --output judged_report.json   # saturation + judge, ONCE per round
        │  ADD_CANDIDATE + REVIEW_AMBIGUOUS tags, split by parent_status (enrichment/routing.py)
        ├── HAS_PARENT ─► taxonomy-pipeline case-a ─► case_a_output.json
        └── NO_PARENT ──► taxonomy-pipeline case-b ─► case_b_output.json
                                      │
                 taxonomy-pipeline merge-preview ─► merge_runs/<timestamp>/
                 validate → deduplicate → resolve → preview → preview.csv
                                      │
                 taxonomy-pipeline bulk-decide ─► decisions.json (clear-cut ambiguous tags)
                 reviewer edits decisions.json, reruns merge-preview --decisions
                                      │  nothing left pending
                 taxonomy-pipeline integrate ─► integrations/<timestamp>/
                 real IDs → additions.sql + baseline copies → enriched.yaml
```

Run from `backend/taxonomy_pipeline/` (the commands default to
`configs/original_vs_skillsfuture.yaml`, `--baseline original` and
`--incoming skillsfuture`):

```bash
taxonomy-pipeline run --config configs/original_vs_skillsfuture.yaml --output judged_report.json  # needs .[llm] + key
taxonomy-pipeline case-a --report judged_report.json
taxonomy-pipeline case-b --report judged_report.json        # needs .[llm] and OPENAI_API_KEY
taxonomy-pipeline merge-preview --input case_a_output.json --input case_b_output.json
# optionally draft decisions for the clear-cut ambiguous tags, then review the rest:
taxonomy-pipeline bulk-decide --merge-run merge_runs/<timestamp> --report judged_report.json \
    --create-below 0.6 --reuse-above 0.9 --output decisions.json
taxonomy-pipeline merge-preview --input case_a_output.json --input case_b_output.json \
    --decisions decisions.json --out-dir merge_runs/reviewed
# score the enriched taxonomy the way the baseline of record was scored, then check it compares:
taxonomy-pipeline integrate --merge-run merge_runs/reviewed --config configs/baseline_v1.yaml
taxonomy-pipeline run --config integrations/<timestamp>/enriched.yaml --output enriched_report.json
taxonomy-pipeline check-comparable baselines/baseline_v1.json.gz enriched_report.json
```

**Run saturation once per round and give both producers the same
`judged_report.json`.** Case A takes the report's `HAS_PARENT` tags and Case B its
`NO_PARENT` tags, each from both `ADD_CANDIDATE` (nothing in the baseline is
close) and `REVIEW_AMBIGUOUS` (something is moderately close, so saturation
can't confirm the tag is new). Together they cover every new or possibly new
tag exactly once; duplicates (`SKIP_*`), `REVIEW_NEW_PLACEMENT` and
`REVIEW_OTHER_LEVEL` tags are not routed. Ambiguous tags are placed like the
others, but every placement is flagged `needs_review` and its `reason` names
the baseline concept saturation found close (`routing.mark_ambiguous`), so
`merge-preview` holds them for a reviewer. On the SkillsFuture run the judge
leaves 140 of the 1,443 `HAS_PARENT` tags and 37 of the 317 `NO_PARENT` tags
ambiguous (1,225 and 185 without it); tags it judges new arrive as
`ADD_CANDIDATE`, unflagged, and its 3 judged duplicates are not routed. Both fail if the report is missing or has no saturation
result for `--incoming`. Each record they write carries the report's `run_id`
as `saturation_run_id`, and `merge-preview` refuses to combine files from
different runs (unstamped files only produce a warning). `judged_report.json`,
`report.json`, `judge_cache.json` and the stage outputs are git-ignored: regenerate them rather than committing them,
since they go stale whenever the data, thresholds or code change.

### Producer contract

Case A and Case B both write a JSON array in the shared contract: nine
fields - `source_dataset`, `tag_id`, `tag`, `original_classification`,
`service`, `category`, `speciality`, `confidence` and `reason`. Unresolved
placements use explicit `null` for service, category, speciality and
confidence. One optional field, `needs_review: true`, marks a placement the
producer can only offer as a candidate (Case A's review candidates), or the
placement of a tag saturation couldn't confirm as new (both producers); it is
omitted when false, and resolution keeps such a tag pending until a reviewer
decides. Extra fields (Case B's `action`, the `saturation_run_id` stamp) are
allowed and ignored by the merge stages. Parse an array with
`CLASSIFICATION_OUTPUTS.validate_json(payload)` from
`taxonomy_pipeline.enrichment.models`; the published schema is
`schemas/classification_output.schema.json`. Field definitions, the merge
stages' rules and the review-decision format are in the
[classification output contract](CLASSIFICATION_OUTPUT_CONTRACT.md).

### Case B (LLM classification)

`case-b` asks an OpenAI model to place each parentless new tag into the
baseline's existing categories and specialities, or to propose a new one,
using the incoming source's context for each tag. That comes from the source's
provider (`--provider`, defaulting to the `--incoming` dataset name; see
`providers/`): for SkillsFuture, the unique-skills workbook's skill
descriptions (`--context`, or the older `--unique`, defaulting to the one in
`backend/skillsfuture/`). Both taxonomies come from
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

Options: `--batch-size` (default 5), `--model`, `--debug-response` (also
shows the endpoint, model and each raw response; Case B logs its progress to
the `taxonomy_pipeline.enrichment.case_b` logger, which the CLI prints),
`--checkpoint`, `--output` (default `case_b_output.json`; a CSV is written next
to it). Completed batches are checkpointed to a hidden file next to the output,
so an interrupted run resumes without paying for finished tags, even with a
different `--batch-size`; a successful run removes it. The checkpoint records
the saturation run, source and model it was written for: rerunning against a
different `report.json` or `--model` stops with an error rather than reusing
old proposals. Checkpoints from older proposal formats that lack required fields
such as `service` are also rejected before any model call; delete the checkpoint
to reclassify those tags. Each record also carries an `action`:
`assign_existing`, `propose_new_speciality` or `propose_new_category`.

### Merge preview

`merge-preview` chains the five merge stages and writes each one's report to
`--out-dir` (default `merge_runs/<timestamp>/`): `validation.json`,
`deduplication.json`, `resolution.json`, `preview.csv` and `run.json` (the
saturation run ID, inputs and review state, which `integrate` records as the
snapshot's provenance).
The `validation.json` file includes per-status `counts` and derived `metrics`,
including total, valid, review, unresolved, duplicate, invalid and skipped
records, plus `valid_rate` and `review_rate`.

- Tags with no duplicate candidates are accepted with their proposed
  placements, provided hierarchy screening also clears them; tags named in a candidate pair, and tags with a `needs_review`
  placement (Case A's review candidates and every ambiguous tag, shown in the
  `needs_review` column of `preview.csv`), stay `pending` until a decision
  covers them. For an ambiguous tag the placement's `reason` names what it
  resembles: decide `create` (it is new), `reuse` with `existing_tag_id` (it is
  that existing tag; `approved_paths` still says where to link it), or
  `exclude`/`defer`. For a review candidate, approve the one path to keep with
  `create` and `approved_paths`
  (see [Resolve reviewed tags and links](CLASSIFICATION_OUTPUT_CONTRACT.md#resolve-reviewed-tags-and-links)
  for the `ReviewDecision` format, and
  `tests/fixtures/classification/review_decisions.json` for an example).
- `--semantic-model all-MiniLM-L6-v2` adds meaning-based duplicate candidates
  (needs `.[semantic]`); without it, only name/spelling candidates are found and
  the preview is marked partial. The model loads at the revision pinned in
  `matching/embeddings.py`, as saturation and breadth do.
- Inputs must follow the nine-field contract; files in the older eight- or
  seven-field formats are reported as invalid rows, not adapted.
- Exit code is non-zero if an input file is unreadable or the preview is
  blocked because the merge would introduce structural errors.

### Hierarchy proposal screening

`merge-preview` screens every complete category and speciality proposal against
the baseline's categories and specialities before resolution. This includes
complete placements held by intake for review; invalid and unplaced rows remain
accounted for in `validation.json`. Incoming tag-pair deduplication still runs
separately.

Screening reuses integrity's `normalize`, saturation's `match_key`, its shared
RapidFuzz scorer and its optional `SemanticMatcher`. Name handling covers case,
whitespace, Unicode dash variants, trailing punctuation, `&`/`and`,
`-isation`/`-ization`, and the matcher's trailing-plural rule. It does not remove
all internal punctuation or apply a general spelling dictionary.

Incoming categories and specialities also share a review hold when distinct
normalised names have the same `match_key` under the same parent and would create
new nodes. Their screening reasons list the proposal IDs in the cluster. Children
of a held category remain held too; tag approvals cannot bypass these holds.
Reject competing proposals before approving the retained label, or correct the
producer paths to one agreed label and rerun screening. Variants that
already resolve to an existing node continue to reuse it.

Each proposal in `resolution.json` under `screening.proposals` contains a stable
ID, original label variants, proposed path, source tags, input locations and all
qualifying baseline matches. Each match retains its existing ID, level, full
branch, match types, lexical score (0-1) and optional cosine score (-1 to 1).
Thresholds default to 0.9 lexical and 0.7 semantic; scores are review evidence,
not probabilities or confirmation of equivalence. Override them with
`merge-preview --screening-lexical 0.95 --screening-semantic 0.8` (finite values
from 0 to 1). Semantic screening also requires `--semantic-model`. These thresholds
are **uncalibrated (#47)** and independent of saturation, Case A and tag
deduplication thresholds: those stages serve different purposes and can disagree.
Settings are saved in `resolution.json` and reused by preview and integration.
The Python runner and resolver accept `screening_settings=ScreeningSettings(...)`.
Duplicate normalised service names in the baseline cause a clear error for an
affected proposal; fix the ambiguous service identities before rerunning.

| Screening status | Effect |
|---|---|
| `reuse` | An unambiguous exact name/spelling variant under the same parent reuses the baseline ID and label. |
| `new` | No candidates from enabled checks; normal tag-resolution rules still apply. |
| `review` | Near matches hold automatic acceptance. Exact matches at another parent/level, ambiguous exact matches and broken exact targets block creating another concept. |

A tag's `create` or `reuse` decision cannot override `blocks_creation` on its
approved category or speciality. When a decision includes safe and blocked paths,
safe paths proceed while blocked paths stay in `unapproved_paths` and appear as
`unapproved` in `preview.csv`. If every approved path is blocked, the sources stay
pending. Review reasons retain producer, baseline-tag and hierarchy context.
Near matches can still be accepted through explicit placement decisions. Issue
#57 also supports explicit hierarchy decisions using the same `ReviewDecision`
model and `--decisions` file as tag decisions; see below. No existing nodes move.

`--semantic-model` enables hierarchy screening with the same matcher used for
tag deduplication. Disabled checks are labelled `disabled`; a failed requested
semantic check holds new concepts until rerun. Preview and integration recheck
the baseline and replay saved screening evidence without loading the model.
**Migration:** saved runs from before this PR have no screening evidence and are
screened again during preview/integration. A previously reviewed run can therefore
fail to integrate when new holds appear. Rerun `merge-preview` with the original
inputs and decisions, review new holds, then integrate the regenerated run. Reports
with screening evidence from an older screening algorithm may also need regeneration.
A new speciality's temporary ID (`new:speciality:<digest>`) is now derived from its
parent category's baseline service name, so runs whose path spells the service
differently (case or spacing) get new IDs: replaying a `resolution.json` saved
before #57 then makes `integrate` report `introduced.resolution_mismatch`. Rerun
`merge-preview` with the same inputs and decisions before integrating.
`deduplication.json` also retains `screening_hierarchy`,
including complete proposals that intake held outside eligible tag resolution.

```text
src/taxonomy_pipeline/enrichment/proposals/
  models.py     # persisted proposal, match and screening shapes
  intake.py     # existing hierarchy groups -> stable proposal identities
  screening.py  # baseline candidates, parent-aware reuse and creation holds
```

The producer contract and JSON Schema are unchanged. Synonym relationships (#56,
after PB07) remain later PB06 work. The saved review API/CLI view is described below.
See the [screening benchmark](docs/proposal-screening-benchmark.md) for full saved
Case A/B input counts, before/after timings and semantic-run measurements.

### Review category and speciality proposals (#57)

Every screened proposal exposes `suggested_action`: `new` suggests `create`,
`reuse` suggests `reuse`, and `review` suggests `defer`. These are suggestions;
the original screening evidence stays separate from the recorded decisions.
Reviewer labels map to the existing actions: approve = `create`, reject =
`exclude`, merge = `reuse`, requires review = `defer`.

Use the existing `ReviewDecision` format in `decisions.json`. A tag decision
still targets `members` and uses `existing_tag_id`. A hierarchy decision targets
one `proposal_id` from `resolution.json`'s `screening.proposals`, and uses
`existing_concept_id` only for `reuse`. Both require a nonblank `reason`.
Do not mix tag fields and hierarchy fields in one record. For example:

```json
[
  {
    "proposal_id": "<copy the proposal_id from screening.proposals>",
    "action": "reuse",
    "existing_concept_id": 42,
    "reason": "Reviewed the definitions: this is the existing speciality"
  }
]
```

Submit tag and hierarchy decisions together using the existing command:

```bash
taxonomy-pipeline merge-preview --input case_a_output.json --input case_b_output.json \
    --decisions decisions.json --out-dir merge_runs/reviewed
```

- A merge target must be a valid existing category or speciality at the proposal's
  level, under the proposal's own parent. The target row and its canonical name
  are reused; no replacement row is created. A category can only reuse an
  existing category in the same service, so its tags never move service; its
  children are rechecked against that category. A speciality in another category
  requires a matching parent-category decision.
- `create` rejects exact/name-key equivalents, unresolved incoming variants,
  blocked parents and failed requested semantic checks. One exception: a
  speciality whose only exact matches are specialities under *other* categories
  can be created, the same cross-parent duplicate-name policy `integrate`
  applies. A same-named category in another service still blocks. A near match
  alone is evidence for review; a reviewer may approve it as distinct.
- Decisions that resolve one of a cluster of incoming spelling variants (`reuse`,
  `exclude`) are applied before the cluster is re-checked, so the remaining
  variant is no longer held for it and can be created or left to resolve. Approved synonyms
  will supply additional equivalence evidence when #56/PB07 is connected.
- `exclude` and `defer` block dependent placements, even if a tag decision approves
  them. They do not reject the source tag itself; use a tag decision to exclude
  that source, or keep another proposed path. Hierarchy approval does not clear
  independent producer or tag-duplicate holds.
- Intake omissions (invalid rows, inconsistent source identities and ambiguous
  paths omitted before deduplication) remain omissions. A hierarchy decision does
  not restore missing tag evidence; correct those inputs and rerun intake.
- Saved reports retain the original screening and the unified `decisions` list.
  Preview and integration replay both. CSV notes show hierarchy decisions and
  the resolved branch when it differs from the producer's supplied path.
- Each validated decision the log doesn't already hold for this baseline appends
  an event to `merge_runs/reviewed/change_log.jsonl`, with a UTC timestamp,
  event/submission IDs, baseline fingerprint and the full decision including its
  reason. Rerunning with the same `decisions.json` plus one new entry logs only
  that entry; an edited decision (new action or reason) is logged again. Reusing
  an output directory preserves earlier events; `--change-log PATH` keeps one log
  across several output directories. Preview/integration replay never logs. Automatic defaults are not recorded
  as human decisions. Invalid submissions fail before reports or events are written.

The JSONL file is a **local PB09 handoff**, not the shared PB09 database integration
or a log of applied taxonomy changes. Keep it with the run artifacts. Shared
storage and reviewer authentication/identity remain PB09 integration work. This
command generates a preview and does not write to the taxonomy database.

```text
src/taxonomy_pipeline/enrichment/
  models.py      # shared ReviewDecision and ReviewDecisionEvent shapes
  review.py      # validate hierarchy decisions and derive effective placements
  change_log.py  # append submitted decisions to the run's local audit log
```

### Read the proposal review view (#58)

Read a saved merge run to see each category/speciality proposal beside every
screening match, with parent branches, concept IDs, match types, lexical and
optional semantic scores. The CLI and API use the same `ProposalReviewReport`
model in `enrichment/models.py` and the same reader in
`enrichment/review_view.py`.

```bash
taxonomy-pipeline review-proposals --merge-run merge_runs/reviewed
taxonomy-pipeline review-proposals --merge-run merge_runs/reviewed --level speciality --offset 50 --limit 50
taxonomy-pipeline review-proposals --merge-run merge_runs/reviewed --proposal-id <proposal_id>
```

`--proposal-id` selects one proposal and can't be combined with `--level` or
`--offset`.

The CLI prints JSON to stdout. The API exposes the same response:

```text
GET /proposals?merge_run=reviewed&level=speciality&offset=0&limit=50
GET /proposals/{proposal_id}?merge_run=reviewed
```

`TAXONOMY_MERGE_ROOT` selects the API's server-side merge directory (default:
`backend/taxonomy_pipeline/merge_runs`, where `merge-preview` writes when run
from `backend/taxonomy_pipeline`, whatever directory the server starts in). `merge_run` is one
directory name beneath that root, not a filesystem path. For custom output
locations, configure the root to their parent directory. The CLI accepts a
local `--merge-run` path as usual. This uses existing files and does not choose
the future governance storage being agreed in #89.

- Each `proposals` item contains `proposed_path` and `proposed_parent` using
  original labels, the full `proposal` screening evidence, the submitted
  `decision` (or null), and its saved `resolved_node` allocation (or null).
  `proposal.path` is the normalized identity; `proposal.variants` retains all
  original spellings. The displayed path uses the same smallest variant as
  screening's display name.
- Every match retains its full existing path and path IDs, including null
  ancestors for broken baseline paths (`reusable=false`). Matches at different
  levels or under different parents remain visible.
- Screening status, `suggested_action`, scores and `same_parent` describe the
  original screening context, before reviewer remapping. `decision` records the
  reviewer's choice separately. A temporary node ID is an allocation, not proof
  of approval, tag readiness or database insertion.
- The envelope includes the baseline fingerprint, screening settings,
  `semantic_status`, skipped checks and pagination (`total`, `offset`, `limit`).
  `total` counts proposals after filtering; pages default to 50, maximum 200.
  All matches for each returned proposal are included. Detail requests return
  the same envelope with one item and `limit=1`.
- Reads do not reload the taxonomy, rerun matching, submit decisions or append
  audit events. They show the saved run, not the current database. Submit choices
  through #57's existing `merge-preview --decisions` and then read the new run.
- Missing runs/proposals return HTTP 404; invalid query parameters return 422.
  Invalid reports, mismatched fingerprints or older runs without screening
  return 409 with an instruction to rerun `merge-preview`; they are not silently
  re-screened. CLI errors return a nonzero exit code.

The dashboard client and proxy accept these read endpoints and share matching
Zod schemas. Review controls and screen layout remain follow-up UI work in #54.

```text
src/taxonomy_pipeline/
  enrichment/review_view.py    # saved evidence -> shared review response
  enrichment/models.py         # ProposalReviewItem / ProposalReviewReport
  api/proposals.py             # read-only list/detail routes under a configured root
```

### Bulk decisions for ambiguous tags

Every ambiguous tag waits for a decision, which on SkillsFuture is over a
thousand tags. `bulk-decide` drafts decisions for the clear-cut ones from
saturation's closest-match similarity (meaning when the report has it, else
spelling rescaled to 0-1) and leaves the middle band for a person:

```bash
taxonomy-pipeline bulk-decide --merge-run merge_runs/<timestamp> --report judged_report.json \
    --create-below 0.6 --reuse-above 0.9 [--decisions decisions.json] --output decisions.json
```

| Option | Drafts |
|---|---|
| `--create-below X` | `create` for an ambiguous tag whose closest matches by meaning *and* spelling all score below `X`, at its single highest-confidence placement |
| `--reuse-above Y` | `reuse` when the closest match (by meaning when the report has it, since a close spelling alone is unconfirmed; else by spelling) is one baseline *tag* scoring at least `Y`, linked at that same placement |

- It only touches pending tags that saturation marked `REVIEW_AMBIGUOUS` and
  that are held for nothing else: their `resolution.json` disposition's
  `reason_codes` is exactly `["producer_flagged"]`. Tags in a duplicate candidate pair, tags
  matching a baseline tag by name, hierarchy screening holds, Case A's review
  candidates for new tags, and tags already in `--decisions` are left as they are.
  A merge run saved before `reason_codes` existed is read from its reason text instead.
- The output starts with `--decisions` and every decision the merge run already
  applied (its `resolution.json` `decisions`, hierarchy decisions included), so
  applying the file alone never drops an earlier approval. A `--decisions` entry
  that changes one of those is refused as a conflict.
- The output is a draft: every decision carries a `Bulk: ...` reason with the
  score and threshold, and goes through `merge-preview --decisions` like a
  hand-written one, so it appears in `preview.csv` and can be edited or removed.
- `--report` must be the report the producers read: it refuses a merge run
  whose `run.json` records a different saturation run, or none (unstamped
  inputs). It also refuses a baseline that changed since the merge run, and
  thresholds outside 0-1. `--output` is never overwritten unless it is also the
  `--decisions` file being extended.
- Historical result **before hierarchy screening**: on a lexical-only SkillsFuture
  run (every ambiguous score is 0.60 or more
  there, since lower spellings are `ADD_CANDIDATE`), `--create-below 0.7`
  drafted 737 creates and left 625 ambiguous tags for review; pending sources
  dropped from 1,749 to 1,012. These counts do not describe the current screening flow.
  No ambiguous tag scored 0.9 against a baseline tag,
  so no reuses. Thresholds are uncalibrated (#47): spot-check a sample of the
  drafted creates before applying them.

### Integrate

`integrate --merge-run DIR` turns a fully reviewed merge run into an enriched
taxonomy snapshot (`--out-dir`, default `integrations/<timestamp>/`; it never
overwrites an existing folder):

```text
integrations/<timestamp>/
├── baseline/*.sql   byte-for-byte copies of the baseline dumps
├── additions.sql    INSERT-only new categories, specialities, tags and links
├── enriched.yaml    config loading baseline + additions as dataset `enriched`, with the round's metrics
└── manifest.json    provenance, counts, ID ranges, duplicate counts, temporary -> real IDs
```

- It reruns the preview's checks against the current baseline first, and
  refuses if the baseline changed since the merge run, an input was unreadable,
  the batch is blocked, **any source is still pending review**, or there is
  nothing to add.
- It also refuses additions the client schema's unique keys would reject:
  `tags.name` is unique (case- and accent-insensitive, `utf8mb4_general_ci`),
  and so is each `speciality_tags` pair. In particular, two sources reviewed as
  separate new tags with the same name (say "Python" the language and "Python"
  the animal) can't both exist. A tag is one shared keyword, so put them in one
  `create` decision approving both paths, `reuse` an existing tag, or exclude
  one.
- New rows get integer IDs after each table's baseline maximum, ordered by
  parent and name, so rerunning on the same merge run gives identical IDs. New
  categories and specialities are written active (`status='1'`), like the
  baseline. Other client columns are left to their database defaults.
- Baseline rows are only copied, never rewritten. `additions.sql` is what would
  be applied to a database matching the baseline snapshot (the snapshot ID is in
  its header); IDs are relative to that snapshot, so don't apply it to a
  database that has changed since. Like the client's phpMyAdmin dumps, it starts
  with `SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO"`, which its backslash escaping
  relies on.
- Before anything is written, the snapshot is reloaded through `sql_dump`. Every
  row must round-trip exactly, and integrity must show no new orphans or dead
  ends. New exact-duplicate names don't block (the baseline already reuses
  speciality names across categories), but the before/after counts are in the
  manifest and printed as a warning.
- Records validation set aside before review (invalid, unresolved, flagged)
  are not in the snapshot; their counts are in the manifest and printed as a
  warning.
- `enriched.yaml` scores the snapshot the way `--config` scores the baseline:
  every metric entry in `--config` that scores `--baseline` (its metric's
  `subject_param`: `dataset` for integrity/depth, `target` for breadth,
  `baseline` for saturation) is copied with that param set to `enriched` and
  every other param kept - breadth's SSIC/SSOC references and thresholds,
  saturation's incoming sources, models and judge settings. The datasets those
  entries read are copied in with their absolute paths, so one
  `taxonomy-pipeline run --config integrations/<timestamp>/enriched.yaml` from
  any directory gives the full scorecard (#72 compares it with the baseline's).
  Entries scoring other datasets are left out, and integrity and depth are added
  with defaults if `--config` has none. The snapshot is named `enriched`, or
  `enriched_2`, ... if a dataset those entries read already has that name (e.g.
  an earlier snapshot used as an incoming source). Saturation's judge `cache` path stays
  relative to the working directory, so run it from `backend/taxonomy_pipeline/`
  to reuse cached verdicts. The config used is recorded as `scoring_config` in
  the manifest's `merge_run` provenance.
- A `metric_definitions_version` pin in `--config` is copied into
  `enriched.yaml` too. To compare the snapshot with the baseline of record
  (PB11), integrate with `--config configs/baseline_v1.yaml`: the default round
  config runs saturation's judge and the baseline doesn't, so their saturation
  results would not compare (see
  [Official metric definitions](#official-metric-definitions-and-the-baseline-of-record)).

## Setup

```bash
cd backend/taxonomy_pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -e .` makes the package importable and gives you the
`taxonomy-pipeline` console command; `[dev]` additionally pulls in `pytest`,
`httpx` (for the FastAPI test client) and `ruff`. Running Case B also needs
the `[llm]` extra (`openai`).

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
`--output judged_report.json` to write the report to a file instead
(git-ignored; about 22 MB for this comparison, because every saturation entry
lists its parents). A name ending in `.gz` is gzip-compressed (about 1.5 MB);
`check-comparable` reads those, but `case-a`, `case-b` and `bulk-decide` need
plain JSON. Saturation runs with the judge on in this config, so it
needs `OPENAI_API_KEY`; reruns read `judge_cache.json` and make no new calls. Every run is also saved to SQLite run history; pass `--no-save` to
skip that.

This config drives classification rounds. The official scorecard of a
taxonomy - the numbers a before/after comparison uses - comes from
`configs/baseline_v1.yaml` instead; see the next section.

## Official metric definitions and the baseline of record

A score depends on the method as much as on the taxonomy: switching breadth to
semantic matching (#79) moved the original taxonomy's industry coverage from 0%
to 22%, and SkillsFuture's occupational coverage from 6.9% to 57.9%, with
neither taxonomy changed. A before/after comparison of an enriched taxonomy
(PB11, #29) only shows the enrichment if both runs were scored the same way, so
the pipeline makes that checkable:

- **Every report records `metric_definitions_version`**, the version of every
  scoring metric's formulas and defaults (`quality/definitions.py`). Reports saved
  before versioning have `null`: unknown, never assumed current.
- **`configs/baseline_v1.yaml` pins the official settings.** Every setting that
  changes a result is written out, saturation's sentence model is pinned to a
  revision (the one in `matching/embeddings.py`'s `PINNED_REVISIONS`, which breadth
  also uses; a test fails if the config's copy drifts from it), and `metric_definitions_version: 1` makes a run fail if the code
  implements another version.
- **The baseline of record is stored** in `baselines/baseline_v1.json.gz`. Run
  history is git-ignored and local, so this file is what everyone compares
  against.
- **`taxonomy-pipeline check-comparable` flags two runs that weren't scored
  alike**, including runs with different definitions versions.

### Definitions v1

The official settings for PB10 (#28). The formulas are documented in #67.

| Metric | v1 method and settings | Why |
|---|---|---|
| `integrity` | Exact duplicates after `normalize`; fuzzy duplicates at RapidFuzz `fuzzy_threshold` 85; orphans; dead ends | Same method and threshold as the audit that first measured the original taxonomy |
| `depth` | Specialities per category and tags per speciality (links with both ends present) | No settings |
| `breadth` | **Semantic.** `sentence-transformers/all-MiniLM-L6-v2` at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, labels only (`semantic_labels_v1`), match at cosine >= 0.60, top 5 candidates kept. SSIC 2025 divisions vs industry categories (`service_id` 1); SSOC 2024 minor groups vs all specialities | Lexical matching (fuzzy 75) scores the original's industry coverage at 0%, so it can't register an improvement. 0.60 is provisional until calibrated (#47) |
| `saturation` | Incoming `skillsfuture`, compared at every level. Close spelling (>= 90) counts only when meaning agrees (>= 0.70; 0.80 for specialities). Add candidate below 60 spelling and 0.50 meaning. The same pinned sentence model as breadth. **Judge off** | The judge is an LLM in public beta, so its verdicts can't be reproduced, and judged ambiguous counts don't compare with unjudged ones. Classification rounds still use it (`judged_report.json`); the scorecard doesn't |

Thresholds are uncalibrated (#47): calibrating them is a definitions change,
so it becomes v2 and the baseline is re-scored.

### Baseline of record (v1)

Run `3d685fb6b1aa4a99b60d256700b4d256` (2026-10-21), stored in
`baselines/baseline_v1.json.gz`, scored Ankit's original taxonomy with
definitions v1:

| Metric | Original taxonomy |
|---|---|
| integrity | 53 rows in exact-duplicate clusters (1 category and 25 speciality clusters); 86 fuzzy pairs; 14 dead-end categories (12.7%), 1 dead-end speciality, 1 unused tag, no orphans |
| depth | 5.39 specialities per category (median 5, n=110); 2.43 tags per speciality (median 2, n=593) |
| breadth | Industry 18/82 SSIC divisions (22%); occupational 16/145 SSOC minor groups (11%) |
| saturation | SkillsFuture new-items rate 15.4%-83.5% of 4,308 concepts (665 add candidates, 2,934 to review); 30 known concepts in a new placement, 32 known at another level |

To check the config still reproduces it, rerun it without touching the record
and compare the summaries (a second run gave identical results):

```bash
taxonomy-pipeline run --config configs/baseline_v1.yaml --no-save --output check.json.gz
```

Sentence-model scores can differ in the seventh decimal place between machines
and devices (CPU vs Apple's MPS differed by up to 3e-7 here), so a concept
sitting exactly on a threshold could in principle land differently.

### Compare an enriched taxonomy with it

```bash
taxonomy-pipeline integrate --merge-run merge_runs/reviewed --config configs/baseline_v1.yaml
taxonomy-pipeline run --config integrations/<timestamp>/enriched.yaml --output enriched_report.json
taxonomy-pipeline check-comparable baselines/baseline_v1.json.gz enriched_report.json
```

`integrate --config configs/baseline_v1.yaml` writes an `enriched.yaml` with
the same settings and the same pin, aimed at the snapshot. `check-comparable
BEFORE AFTER` takes report files (`.json` or `.json.gz`) or run IDs from run
history. It is read-only: an argument that looks like a path (has a `/` or a
`.json`/`.gz` suffix) and doesn't exist fails as a missing file, and a run ID is
looked up only when run history exists, so the check never creates the
database. It prints `[OK]` or `[FLAGGED]` per result and exits non-zero if
anything is flagged:

| Flag | Meaning |
|---|---|
| `definitions_version` | Scored with different definitions versions, or one run predates versioning: nothing compares |
| `run_status` | A run failed |
| `missing_result` | A metric scored the taxonomy in one run only, or a run has no result scoring its subject at all (e.g. a mistyped `--before-subject`), so nothing would be compared |
| `result_status` | A result is `error` or `skipped` |
| `settings` | Same metric, different params (all but the scored taxonomy's `dataset`/`target`/`baseline`); the changes are listed |
| `reference_data` | A dataset the result reads (SSIC, SSOC, the incoming source) has different row counts |

Each run's subject, the taxonomy it scores, is read from its results. Name it
with `--before-subject`/`--after-subject` for a run that scores several (e.g.
one from `original_vs_skillsfuture.yaml`). The library call,
`definitions.check_comparable`, also pairs each result with its counterpart;
the comparison report (#69) builds on it. Row counts are the only data check,
so reference data edited without changing its size isn't caught.

### Changing a definition

Any change to how a scoring metric scores is a new definitions version. That
includes a formula, a normalisation step, a matching tier, a default, or a
pinned model or text profile.

1. Bump `METRIC_DEFINITIONS_VERSION` in `quality/definitions.py`.
2. Pin the new `definitions_snapshot()` in `tests/test_definitions.py` under
   the new number. That test fails on any changed default or pinned constant
   until you do. Formula changes have no such tripwire, so call them out in
   the PR.
3. Copy the latest baseline config to `configs/baseline_v<N>.yaml`, apply the
   change and bump its pin. Keep the old config as history.
4. Score it with `taxonomy-pipeline run --config configs/baseline_v<N>.yaml
   --output baselines/baseline_v<N>.json.gz`, then update the run ID and
   numbers above (a test checks this README names the stored run).
5. Record the decision on the issue.

A config pinned to an older version then fails with an explanation rather
than quietly producing new-version numbers. To reproduce an old baseline, run
its config from a commit that implemented that version.

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
new_placement_rate, review_other_level, cumulative_known_concepts}`, plus
`review_ambiguous_before_judge` and `judge_counts` when the judge is on.
Matches also report `matched_source` and `baseline_status`; matches to
inactive (`status='0'`) rows are counted in `matched_inactive`.

### Saturation settings

Defaults come from `SaturationMetric.default_params`
(`src/taxonomy_pipeline/quality/saturation.py`); the last column is what
`configs/original_vs_skillsfuture.yaml` uses.

| Param | Default | Meaning | SkillsFuture config |
|---|---|---|---|
| `baseline` | - | Existing taxonomy dataset name | `original` |
| `incoming` | - | One dataset name or an ordered list; each is one point on the curve | `[skillsfuture]` |
| `threshold` | 90 | RapidFuzz score (0-100) for a "close spelling"; never enough on its own | 90 |
| `semantic_model` | none | Optional sentence-transformers model; without it only exact/spelling evidence is used | `all-MiniLM-L6-v2` |
| `semantic_model_revision` | none | Hugging Face commit, branch or tag to load `semantic_model` at. Unset, a model pinned in `matching/embeddings.py` (`all-MiniLM-L6-v2`, by its short or full id) loads at that pin, the same one breadth uses; any other model follows its default branch, which can move. The revision loaded is recorded in `details` | none (`configs/baseline_v1.yaml` writes the pin out) |
| `semantic_threshold` | 0.70 | Cosine similarity for a meaning match; also used by the parent check | 0.70 |
| `semantic_threshold_by_level` | `{}` | Per incoming level override of `semantic_threshold` for concept matching | `{specialities: 0.80}` |
| `add_candidate_lexical_threshold` | 60 | `ADD_CANDIDATE` needs the best spelling score at every level below this | 60 |
| `add_candidate_semantic_threshold` | 0.50 | ...and (with a model) the best meaning score below this; otherwise `REVIEW_AMBIGUOUS` | 0.50 |
| `cross_level` | true | Also search the other levels of the known taxonomy | true |
| `judge` | none (off) | Optional LLM judge for `REVIEW_AMBIGUOUS` concepts; see [Decisions API judge](#decisions-api-judge) | on: `gpt-6-luna`, `[tags]`, cache `judge_cache.json` |

### Decisions API judge

The spelling and meaning scores can't tell "the same skill, worded differently"
from "a different skill in the same field", so most SkillsFuture tags land in
the `REVIEW_AMBIGUOUS` band. With `judge` set, saturation sends each
`REVIEW_AMBIGUOUS` concept to OpenAI's Decisions API (`POST /v1/decisions`,
public beta) with its closest known concepts by meaning, each with up to three
parents as context, and asks one `choice` question: the same concept as
candidate N, or none?

- Probability of candidate N >= `same_at_or_above` -> duplicate:
  `SKIP_SEMANTIC_DUPLICATE` with `match_type: "judged"`, listed in
  `judged_match_details` (parent check as for any match).
- Probability of "none" >= `new_at_or_above` -> `ADD_CANDIDATE`, `reason: judged_new`.
- Otherwise it stays `REVIEW_AMBIGUOUS` with `reason: judge_unsure`, or
  `judge_failed` when the request failed after the SDK's retries.

Every judged entry carries `judge: {model, choice, probability, candidates}`.
`ADD_CANDIDATE` and duplicates are never sent.

| `judge` key | Default | Meaning |
|---|---|---|
| `model` | `gpt-6-luna` | Decisions API model |
| `candidates` | 5 | Closest known concepts (by meaning) offered per concept |
| `same_at_or_above` | 0.8 | Probability needed to call it a duplicate |
| `new_at_or_above` | 0.8 | Probability of "none" needed to call it new |
| `levels` | `[tags]` | Levels the judge runs on |
| `cache` | none | JSON file of verdicts keyed by request; reruns reuse them instead of paying again (git-ignored as `judge_cache.json`) |
| `workers` | 8 | Parallel requests |

Setup: `pip install -e ".[llm]"`, `OPENAI_API_KEY` in `backend/.env` (the same
key Case B uses), and `semantic_model` set (it picks the candidates). It is on
in `configs/original_vs_skillsfuture.yaml`; its report is `judged_report.json`
(see [Classification workflow](#classification-workflow)). One request per
ambiguous concept: about 1,410 for SkillsFuture tags, roughly $0.04 at
$0.10/1M input tokens, and free on a cached rerun. A request the API rejects
outright (bad key, unknown model or endpoint) stops the run with a `judge:`
error; other failures only mark that concept `judge_failed`.

Things to keep in mind:

- **It changes the metric.** The ambiguous count, new-rate range and curve
  move when the judge is on. Levels it ran on also report
  `review_ambiguous_before_judge` and `judge_counts` (`asked`,
  `judged_duplicate`, `judged_new`, `unsure`, `failed`), and each curve point
  `review_ambiguous_before_judge`, so the pre-judge figure is never lost.
  Compare baseline and enriched runs only with identical judge settings.
- **Reproducibility comes from the cache.** The model is in public beta and
  can change; keep the cache file with a report you want to reproduce.
- **Downstream:** judged duplicates are not routed to Case A/B; judged-new tags
  are routed as `ADD_CANDIDATE`, so they reach merge-preview without a review
  flag; only unsure or failed tags keep it. Case A's own matching
  (`category_adaptation`) never uses the judge.

### Reading the saturation report

For the integration team: everything is under
`results[metric_id == "saturation"].details.by_concept_level.<categories|specialities|tags>`
(the last incoming source; `details.steps[i]` has the same block per source).
Each incoming concept appears in exactly one list:

| Verdict (`recommended_action`) | List | What to do |
|---|---|---|
| `SKIP_DUPLICATE` | `exact_match_details`, `fuzzy_match_details` | Nothing - already in the taxonomy |
| `SKIP_SEMANTIC_DUPLICATE` | `semantic_match_details`, `judged_match_details` (judge only) | Nothing (spot-check meaning and judge matches if in doubt) |
| `REVIEW_NEW_PLACEMENT` | `new_placement_details` | Known concept, new parent: decide whether to add it under `unmatched_incoming_parents` |
| `REVIEW_OTHER_LEVEL` | `other_level_match_details` | Similar concept at another level: confirm it really is the same concept |
| `ADD_CANDIDATE` | `ambiguous_concepts` | New: decide where to place it, using `parents` |
| `REVIEW_AMBIGUOUS` | `ambiguous_concepts` | Partly similar: check against `closest_baseline_name` / `closest_semantic_baseline_name` |

`reason` values: `no_close_match` and `judged_new` (ADD), `partial_similarity`,
`close_spelling_unconfirmed`, `judge_unsure` and `judge_failed` (REVIEW_AMBIGUOUS), `parent_differs` and
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

with open("judged_report.json") as f:
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
run flagged as new or possibly new (`ADD_CANDIDATE` or `REVIEW_AMBIGUOUS`) **and** that have a parent in
their own source (saturation's `parent_status == HAS_PARENT` on that tag -
tags with `NO_PARENT` have nothing of their own to place and are Case B's job
instead, not this metric's).

A source's levels don't have to mean what the baseline's do. In SkillsFuture
the tag is a skill, its speciality a job role and its category a sector,
while the baseline's specialities are themselves skills. Placing a tag through
its source specialities would propose job titles as specialities, one row per
job role (up to ~100 per skill), so Case A never uses source specialities as
targets. It asks what the tag *is*, the same question Case B asks, reusing
saturation's exact/lexical/semantic matcher rather than reimplementing it:

1. **Content** (`placement_basis: content`): the tag itself is matched
   against every baseline speciality. A confident match is the placement -
   one row, under the category and service that speciality really sits
   under, whatever the tag's source category says.
2. **Source category fallback** (`placement_basis: source_category`): a tag
   that matches nothing is placed via its source category (a SkillsFuture
   sector), one row per distinct source category. The source category is
   matched against every baseline category; a confident match reuses that
   category's real ID, no confident match proposes a `NEW_CATEGORY`. Under a
   resolved category the tag itself is proposed as a `NEW_SPECIALITY` (step 1
   already searched every baseline speciality, so none fits). The source
   specialities linking the tag to that category are evidence only:
   `source_speciality_count` and `source_speciality_sample` on the placement,
   and named in the record's `reason`.

3. **Review candidates** (`placement_basis: review_candidate`): a source
   category that only partly matches (`REVIEW_AMBIGUOUS`,
   `REVIEW_NEW_PLACEMENT`, `REVIEW_OTHER_LEVEL`) is never auto-resolved - that
   would look like a confident placement when the foundation under it isn't
   confirmed. Instead the tag is proposed under each of the **3 closest
   baseline categories** (by meaning with a semantic model, else by
   spelling), as a new speciality named after itself, every record flagged
   `needs_review: true` with confidence set to the candidate's similarity
   (source category name vs candidate category name). Resolution never
   accepts a flagged tag by default, so it shows as `pending` in
   `merge-preview` until a reviewer approves one candidate path with a
   `create` decision, or excludes/defers the tag; `integrate` refuses while
   any are pending. A tag placed via one source category and offered for
   review via another is held as a whole, so its decision approves every
   path it should keep.

Only an invalid source category name, a tag with no source category, or a
new category with no service to vote leaves a record unresolved (all four
placement fields null, with a reason).

On the SkillsFuture run (221 tags) this gives 400 records instead of 1,768, no
job titles proposed as specialities, and every tag accounted for: 147 placed
automatically, 64 offered only as review candidates (previously emitted empty
and dropped by `merge-preview`), and 10 placed via one sector with candidates
from another. None matches an existing speciality by name: being
`ADD_CANDIDATE`, none is close to one.

### Params

Defaults come from `CategoryAdaptationMetric.default_params`
(`src/taxonomy_pipeline/enrichment/category_adaptation.py`).

| Param | Default | Meaning |
|---|---|---|
| `baseline` | - | Existing taxonomy dataset name |
| `incoming` | - | The one source dataset name being adapted |
| `new_tag_ids` | - | Required; the tag IDs to place (saturation's `ADD_CANDIDATE` and `REVIEW_AMBIGUOUS` + `HAS_PARENT` tags) |
| `threshold` | 90 | RapidFuzz score (0-100) for a "close spelling"; never enough on its own |
| `semantic_model` | none | Optional sentence-transformers model; without it only exact/spelling evidence is used |
| `semantic_threshold` | 0.6 | Cosine similarity for a meaning match, used for both categories and specialities. Lower than saturation's tag default (0.70): categories are broader, less precisely-worded concepts, and real runs show correct matches scoring as low as 0.63-0.69 |
| `add_candidate_lexical_threshold` | 60 | Below this *and* the semantic threshold, nothing is even close - `NEW_CATEGORY`/`NEW_SPECIALITY` instead of `REVIEW_AMBIGUOUS` |
| `add_candidate_semantic_threshold` | 0.5 | See above |

### Actions

Every placement has an `action`: the category-level outcome for the
fallback, or the speciality match for a content placement. A resolved
placement also has a `speciality_action`:

| Action | Level | Meaning |
|---|---|---|
| `SKIP_DUPLICATE` / `SKIP_SEMANTIC_DUPLICATE` | category, speciality | Matched an existing baseline node; reuse its real ID |
| `NEW_CATEGORY` | category | No existing category matches the source category; propose it as a new one |
| `NEW_SPECIALITY` | speciality | The tag matches no existing speciality; propose the tag itself as one, under the resolved category |
| `REVIEW_AMBIGUOUS` | category | Partial similarity at best; both the closest lexical and closest semantic baseline names are surfaced, and the tag is offered under the closest categories for review |
| `REVIEW_NEW_PLACEMENT` | category | Matches a known category, but under a different parent than the source gives it (`unmatched_incoming_parents` vs `existing_parents`); offered for review as above |
| `REVIEW_OTHER_LEVEL` | category | Matches a known concept at a different taxonomy level; offered for review as above |
| `INVALID_CATEGORY_NAME` | category | Source category name is blank or a placeholder |
| `NO_SOURCE_CATEGORY` | tag | The tag matches no speciality and has no source category in this dataset - Case A has nothing to place it against; route to Case B |

A tag that fell back after a near miss in the content step also carries
`closest_speciality_name` / `closest_semantic_speciality_name` and their
scores, named in its `reason`.

A resolved fallback category (`SKIP_*` or `NEW_CATEGORY`) also carries `target_service_id`/
`target_service_name`: looked up directly from the baseline for a matched
category, or guessed by majority vote across whichever *other* source
categories matched in the same run for a brand new one (`None` if nothing
else in the run matched anything to vote from - the placement then stays
fully unresolved, since the classification contract requires service,
category, speciality and confidence to be all-present or all-null together).
A content placement takes its service from the matched speciality's category,
and a review candidate from its candidate category (`candidate_category_*`,
`candidate_service_*`, ranked by `candidate_rank` with `candidate_score` /
`candidate_score_type`).

### Running it end to end

`taxonomy-pipeline case-a` reads the tag IDs from a saved saturation report
(`ADD_CANDIDATE` or `REVIEW_AMBIGUOUS`, + `HAS_PARENT`; see [Classification workflow](#classification-workflow)),
runs `category_adaptation` on them, serializes the placements via
`enrichment.from_category_adaptation.build_classification_outputs`, and
validates the result against the contract before writing it out
(`case_a_output.json` by default). `--semantic-model ''` runs it lexically only;
a model pinned in `matching/embeddings.py` loads at that pin.

```bash
taxonomy-pipeline run --config configs/original_vs_skillsfuture.yaml --output judged_report.json
taxonomy-pipeline case-a --report judged_report.json --output case_a_output.json
```

## Industry and occupational breadth

The `breadth` metric defaults to **semantic matching** and returns separate
estimated industry and occupational coverage. It compares SSIC division titles
against industry category names (`service_id=1`), and SSOC minor-group titles
against all named specialities. Reference levels and target selection are the
same in lexical and semantic modes; no hierarchy roll-up is performed. Semantic
matching at 0.60 is the official method for definitions v1 (see
[Official metric definitions](#official-metric-definitions-and-the-baseline-of-record)):
compare only results scored with the same method and threshold.

Semantic matching uses local `sentence-transformers/all-MiniLM-L6-v2`, pinned to
revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. Install updated dependencies
with `python -m pip install -e ".[dev]"` in this project directory. The first
semantic run downloads model files to the standard Hugging Face cache and needs
network access; subsequent runs reuse cached files. Text is embedded locally on
CPU in batches of 32, with one lazily loaded model per process. Lexical runs do
not load the model. Model/dependency failures produce metric errors, not fallback
lexical scores.

Only labels are embedded. The `semantic_labels_v1` text profile applies Unicode
NFKC and collapses whitespace while preserving case and punctuation; it does not
use parent context, aliases, descriptions, or tags. Unit-normalized vectors are
compared with cosine similarity. Each reference is matched to its highest-scoring
target if the **unrounded score** meets `semantic_threshold` (default `0.60`).
This cutoff is **provisional and uncalibrated**, not a probability or a conversion
of fuzzy threshold 75. The top `top_k` candidates (default 5) are retained even
for unmatched references, allowing review and later calibration.

To use the previous matcher, set `matching_method: lexical`. Its `threshold`
defaults to 75 on a 0–100 RapidFuzz scale, with `preprocessing: typography_v1`.
For exact reproduction of the original baseline, also set `preprocessing: legacy`.
Lexical mode ignores `semantic_threshold` and `top_k`; semantic mode ignores
`threshold` and the lexical `preprocessing` parameter.

The lexical `typography_v1` profile applies Unicode NFKC and case folding,
converts curly quotes to straight quotes and Unicode hyphens/dashes
(U+2010–U+2015) to `-`, and collapses whitespace. It removes spaces around `/`,
`-`, and `&`, and just inside parentheses and square brackets. Commas, colons,
and semicolons have no preceding space and one following space, except before a
closing bracket or the end of the label. Other punctuation, words, qualifiers,
and symbols such as `C++`, `C#`, and `M&A` are retained. `legacy` only lowercases,
trims, and collapses whitespace. Integrity and saturation retain their existing
normalization and lexical matching.

All breadth scores are estimates, not verified semantic coverage. Original
specialities describe capabilities, whereas SkillsFuture specialities typically
describe occupations. Related labels need not demonstrate occupational coverage;
review candidate pairs and calibrate the cutoff before interpreting scores as
validated coverage. Existing saved reports are not rewritten.

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
      matching_method: semantic
      semantic_threshold: 0.60  # uncalibrated cosine cutoff
      top_k: 5
```

Each reference requires exactly one workbook path. The old `reference`
parameter is no longer accepted; replace it with `ssic_reference` and
`ssoc_reference`. Other metrics continue accepting taxonomy datasets only.

Results contain `details.target`, `details.industry_service_id`, the effective
`details.matching_method` and `details.preprocessing`, plus separate `industry`
and `occupational` objects. Each dimension reports the reference/version/level,
effective threshold, target count, matched/total counts, coverage, and matched
and unmatched records. `score_type` is `cosine_similarity` (−1 to 1) for semantic
matching and `fuzz_ratio` (0 to 100) for lexical matching.

Semantic reports include the model ID/revision, `threshold_calibrated: false`,
and `top_k`. Both accepted and rejected references contain `candidates` and
`normalized_reference` (the exact embedding text). Candidate records retain the
target ID/name, `normalized_target` embedding text, and score rounded to four
decimal places. Accepted records also retain the existing best-match fields.
Lexical matches retain their original fields and normalized matching inputs.
Original source labels are never modified.

There is no combined score. Each reference code counts once; several reference
codes may match the same target node. Duplicate target names retain distinct
rows, and ties use source row order. Empty targets score zero; empty reference
levels return null coverage. Reports remain compatible with CLI, API and SQLite
storage without a database migration.

## Run it as a service

```bash
taxonomy-pipeline serve --reload
```

Every request needs the API token configured for that server, sent in an
`Authorization: Bearer <token>` header. Set it in `backend/.env`, which
`taxonomy-pipeline serve` loads at startup, or in the server's environment,
which wins over the file. (Starting `uvicorn taxonomy_pipeline.api.app:app`
directly skips the file, so export the variables yourself in that case.)

| Env var | Default | Meaning |
|---|---|---|
| `TAXONOMY_API_TOKEN` | *(none)* | The token every request must send. With no token set, **every request gets 401**, so a forgotten setting can't leave the API open. The `.env.example` placeholder (`change-me`) counts as no token: replace it. |
| `TAXONOMY_API_AUTH` | *(on)* | Set to `off` to skip the token check, for local development on your own machine only. |
| `TAXONOMY_DATA_ROOTS` | `backend/` | Directories (separated by `:` on macOS/Linux) that `POST /runs` may read dataset files from. Paths are resolved first, so `..` and symlinks can't escape. The default covers the bundled SQL dumps, SkillsFuture data, reference workbooks and `integrations/` snapshots, but only when the package is installed in editable mode (`pip install -e .`); otherwise set it explicitly. |

```bash
curl -H "Authorization: Bearer $TAXONOMY_API_TOKEN" http://localhost:8000/metrics
```

**Who needs which token.** A token belongs to one running API server, not to
the project:

- **Everyone running the API on their own machine** (the usual setup) picks
  their own token, or sets `TAXONOMY_API_AUTH=off`. Only their own dashboard
  calls that API, so nobody needs anyone else's token.
- **One server that several people or dashboards call** (e.g. a deployed demo)
  has one token. Whoever runs the server generates it and shares it privately
  with the people who use it, never in git or a group chat. Changing it means
  telling all of them.

The dashboard's proxy adds the token server-side (see `frontend/README.md`),
so give the dashboard that calls this API the same `TAXONOMY_API_TOKEN`. The token check lives in
`api/security.py` and is attached to the whole app, so a new route is
covered automatically. The interactive `/docs` page and `/openapi.json` stay
public (they describe the API but run nothing), and the docs page can't send
the token, so use curl or the dashboard to call endpoints.

- `GET /metrics` - self-describing catalogue of what's runnable.
- `POST /runs` - body is a `PipelineConfig` (the same schema as the YAML
  configs, as JSON); dataset paths must be absolute paths on the server,
  inside `TAXONOMY_DATA_ROOTS` (anything else is rejected with 422). A config
  that enables the LLM judge (any metric's `judge` param) is also rejected
  with 422: a judged run spends OpenAI credits and writes its cache file on
  the server, so run judged scoring from the CLI. It returns
  `{run_id, status: "queued"}` immediately and runs the pipeline in the
  background.
- `GET /runs/{run_id}` - poll for status, which moves `"queued"` ->
  `"running"` -> `"done"` or `"failed"`; once finished, the full report is
  in the response.
- `GET /runs` - recent run history.
- `GET /proposals?merge_run=&level=&offset=&limit=` and
  `GET /proposals/{proposal_id}?merge_run=` - saved hierarchy proposals with
  parent branches, matching evidence and decisions (see the #58 review view above).
- `GET /taxonomy/search?run_id=&dataset=&q=&level=&limit=` - name search
  (case/punctuation-insensitive, via the same `normalize` `integrity` uses)
  over a dataset loaded by a past run, optionally filtered by `level`
  (`services`/`categories`/`specialities`/`tags`). `q` omitted lists
  concepts unfiltered, up to `limit` (default 50, max 200) - a browse, not
  a search.
- `GET /taxonomy/{level}/{concept_id}?run_id=&dataset=` - one concept's
  parent path(s) up to its service, its direct children one level down, and
  (for a speciality) its linked tags. A tag gets one path per speciality
  it's linked to, since it can be linked to more than one; an orphan row
  (e.g. a speciality whose category doesn't resolve) gets a shorter path
  rather than an error, the same row `integrity` would report as an orphan.

Both `/taxonomy` endpoints re-read the dataset from disk on every call, using
the file paths saved in that run's own config - nothing is kept loaded
between requests, and no snapshot of what the run originally loaded is kept
either. So a result reflects the source files' *current* contents, not
necessarily what that run saw: editing a dataset file changes what the same
`run_id` returns immediately, with no new run needed.

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
implement `TaxonomySource.load()` (see `taxonomy/base.py`) and register a
builder for it in `taxonomy/__init__.py`. No other file changes.

If Case B should classify tags from the new source, also add a
`SourceProvider` (see `providers/base.py`) supplying each tag's prompt context,
and register it in `providers/registry.py` under the source's dataset name.

## Plugging in a metric

Subclass `Metric` (see `quality/base.py`), implement `run(datasets, params)
-> MetricResult`, and add one line to `quality/registry.py`. It's then
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

A metric that scores one taxonomy sets `subject_param` (the param naming it).
That makes it part of the metric definitions version and of `check_comparable`:
adding one, or changing its defaults or formula, means a new version (see
[Changing a definition](#changing-a-definition)). Override `definition()` to
pin anything its scores depend on that no param exposes, as breadth does for
its sentence model.

## Config and report reference

**Config** (`config.py`) - what the CLI reads from YAML and what `POST
/runs` accepts as JSON. YAML paths can be relative to the YAML file; API
paths must be absolute server-side paths inside `TAXONOMY_DATA_ROOTS`:

```yaml
metric_definitions_version: 1 # optional pin: the run fails if the code implements another version
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
  "metric_definitions_version": 1,  // formulas and defaults it was scored with; null on reports from before versioning
  "error": null,              // set only when status == "failed" (e.g. a dataset failed to load)
  "datasets": [{"name": "original", "counts": {"categories": 110, ...}}],
  "results": [
    {
      "metric_id": "integrity",
      "status": "ok",         // "ok" | "error" (bad params/dataset)
      "summary": "...",        // one-line, human-readable
      "details": {...},        // metric-specific structured data - see that metric's module for its shape
      "index": 0,              // position of the producing entry in the config's `metrics` list
      "params": {"dataset": "original", "fuzzy_threshold": 85}  // that entry's params over the metric's defaults
    }
  ]
}
```

`index` and `params` say which config entry produced a result, since one
config can run the same metric more than once (integrity once per dataset).
They are `null` on reports saved before they existed. The CLI summary and the
dashboard's run page label a repeated metric by the params that differ between
its runs, e.g. `[OK] integrity (dataset=skillsfuture): ...`
(`pipeline.result_labels`, mirrored by the dashboard's `metricLabels`).

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
or across tests. Helpers several test modules share (e.g. `breadth_datasets`,
`review_candidate_file`) also live in `conftest.py`; a test module never imports
another test module.

Ruff linting and the test suite run in CI (see the root
`.github/workflows/ci.yml`).

## Known gaps

- **Ambiguous tags make a large review queue:** routing every
  `REVIEW_AMBIGUOUS` tag holds each one for a decision. A historical lexical-only
  SkillsFuture run before hierarchy screening gave 1,749 pending sources and 5,769
  flagged Case A records (up to 48 for one tag: 16 partly matched sectors x 3 candidates).
  `bulk-decide` settled the clear-cut ones (1,749 -> 1,012 pending at
  `--create-below 0.7`), but its thresholds are uncalibrated; calibrating
  saturation's thresholds (#47) so clearly new tags become `ADD_CANDIDATE` is
  what shrinks the queue properly.
- **Every created ambiguous tag becomes its own speciality:** Case A proposes a
  skill with no matching speciality as a new speciality named after itself, so
  the historical 737 bulk creates above added about as many specialities, many
  close to an existing one (e.g. created at 0.69 spelling similarity to 'Performance
  Management'). Whether a loosely similar skill should instead be linked under
  the speciality it resembles is a team decision.
- **Case A matches tags by name only:** the content step compares the tag
  name with baseline speciality names, so a skill whose name shares nothing
  with its natural home (e.g. a functional skill used in one sector) falls
  back to its sector's industry category. Case B reads the skill description;
  embedding it here too is a possible follow-up.
- **Case B service selection:** the model now selects a service from the
  baseline's allowed services and receives each speciality's service context in
  its prompt. The output conversion preserves that selection, so categories
  are no longer implicitly moved under `Industry`. The merge stages still
  validate the complete service/category/speciality path.
- **No promotion step:** `integrate` writes and checks an enriched snapshot,
  `enriched.yaml` scores it, and `check-comparable` confirms that run is scored
  like the baseline of record. The before/after comparison report (#69) and
  promoting the snapshot to the next baseline are still the incremental flow
  in #49 (and the PB11 evaluation, #29).

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
- The fuzzy layer of **`integrity`** uses lexical (RapidFuzz) matching only.
  `breadth` matches semantically by default, and saturation optionally adds a
  `sentence-transformers` embedding model for semantic candidates.

- No live-database source yet - sources are `sql_dump`, `ssic`, and `ssoc`. `backend/audit/` already
  has a working live-DB connection (env vars `MYSQL_HOST` etc.) worth
  reusing when that source gets built, rather than reinventing it.
