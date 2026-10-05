# Taxonomy validation pipeline

Scores taxonomy quality (integrity, breadth, depth, saturation) so Ankit's
original taxonomy and the team's SkillsFuture-generated one can be compared
on the same basis. Runs as a config-driven CLI, and as a FastAPI service so
a future admin dashboard (or the client, if scope changes again) can drive
it over HTTP.

The CLI and the API call the exact same `pipeline.run_pipeline` - there is
one implementation of "what a run does," not two.

## Project structure

```text
taxonomy_pipeline/
├── configs/                          # example pipeline configs
├── src/taxonomy_pipeline/
│   ├── datasets/                     # pluggable taxonomy sources
│   │   ├── base.py                   #   TaxonomySource protocol, canonical TaxonomyDataset
│   │   └── sql_dump.py               #   reads plain .sql dump files (today's only source)
│   ├── metrics/                      # pluggable quality checks
│   │   ├── base.py                   #   Metric ABC
│   │   ├── integrity.py              #   duplicates, orphans, dead ends
│   │   ├── depth.py                  #   branching factor
│   │   ├── breadth.py                #   coverage vs. a reference dataset
│   │   ├── saturation.py             #   discovery-curve vs. real gig/showcase text
│   │   └── registry.py               #   METRICS dict - register new metrics here
│   ├── config.py                     # YAML/JSON pipeline config (datasets + metrics)
│   ├── pipeline.py                   # run_pipeline(config) -> RunReport
│   ├── db.py                         # SQLite run history
│   ├── api/app.py                    # FastAPI: /metrics, /runs
│   └── cli.py                        # `taxonomy-pipeline run|serve`
└── tests/
```

## Setup

```bash
cd backend/taxonomy_pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -e .` makes the package importable and gives you the
`taxonomy-pipeline` console command; `[dev]` additionally pulls in `pytest`
and `httpx` (for the FastAPI test client).

## Run it against the two real datasets already in this repo

```bash
taxonomy-pipeline run --config configs/original_vs_skillsfuture.yaml
```

This loads `backend/sql-files-nus/*.sql` (Ankit's original taxonomy) and
`backend/skillsfuture/skillsfuture_taxonomy.sql` (the generated SkillsFuture
taxonomy's `sf_*` tables), runs integrity + depth on each, evaluates both
taxonomies against SSIC 2025 and SSOC 2024 for breadth, and reports `saturation`
as skipped (no gig/showcase text corpus exists yet). Prints the full JSON
report to stdout and a one-line-per-metric summary to stderr; pass
`--output report.json` to write the report to a file instead.

## Industry and occupational breadth

The `breadth` metric defaults to **semantic matching** and returns separate
estimated industry and occupational coverage. It compares SSIC division titles
against industry category names (`service_id=1`), and SSOC minor-group titles
against all named specialities. Reference levels and target selection are the
same in lexical and semantic modes; no hierarchy roll-up is performed.

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

- `GET /metrics` - self-describing catalogue of what's runnable.
- `POST /runs` - body is a `PipelineConfig` (the same shape as the YAML
  configs, as JSON); returns `{run_id, status: "queued"}` immediately and
  runs the pipeline in the background.
- `GET /runs/{run_id}` - poll for status; once `"done"` or `"failed"`, the
  full report is in the response.
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
/runs` accepts as JSON, same shape either way:

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
      "status": "ok",         // "ok" | "skipped" (e.g. saturation with no corpus) | "error" (bad params/dataset)
      "summary": "...",        // one-line, human-readable
      "details": {...}         // metric-specific structured data - see that metric's module for its shape
    }
  ]
}
```

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

No linter/formatter is configured yet for this subproject specifically; the
CI job just runs the test suite (see the root `.github/workflows/ci.yml`).

## Known gaps

- **`saturation`** needs real (or synthetic) gig/showcase text via
  `params.corpus_path`; none exists yet, so every config has it wired up but
  reporting "skipped" until a corpus does.
- **`breadth`** now defaults to semantic label matching with an uncalibrated
  threshold. Its embedding component is reusable, but **`saturation`** and the
  fuzzy layer of **`integrity`** still use lexical RapidFuzz matching. Hierarchy
  aggregation, reviewed mappings, threshold calibration, and integration with
  the matching microservice remain future work.
- No live-database source yet - sources are `sql_dump`, `ssic`, and `ssoc`. `backend/audit/` already
  has a working live-DB connection (env vars `MYSQL_HOST` etc.) worth
  reusing when that source gets built, rather than reinventing it.
