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

## Run it against the two real datasets already in this repo

```bash
taxonomy-pipeline run --config configs/original_vs_skillsfuture.yaml
```

This loads `backend/sql-files-nus/*.sql` (Ankit's original taxonomy) and
`backend/skillsfuture/skillsfuture_taxonomy.sql` (the generated SkillsFuture
taxonomy's `sf_*` tables), runs integrity + depth on each, breadth of
`original` against `skillsfuture` as the reference, and reports `saturation`
as skipped (no gig/showcase text corpus exists yet). Prints the full JSON
report to stdout and a one-line-per-metric summary to stderr; pass
`--output report.json` to write the report to a file instead.

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
    type: sql_dump          # the only source implemented today
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

## Known gaps (see the `senseigigs-*` project memory for the full picture)

- **`saturation`** needs real (or synthetic) gig/showcase text via
  `params.corpus_path`; none exists yet, so every config has it wired up but
  reporting "skipped" until a corpus does.
- **`breadth`**, **`saturation`**, and the fuzzy layer of `integrity` use
  lexical (rapidfuzz) matching only - deliberate baselines. The plan is a
  shared `sentence-transformers` embedding layer for semantic matches (e.g.
  'M&A' vs 'Mergers and Acquisitions'), used by both this pipeline and the
  FastAPI matching microservice, so "how we proved it's accurate" and "how
  we score it live" don't drift into two implementations.
- No live-database source yet - only `sql_dump`. `backend/audit/` already
  has a working live-DB connection (env vars `MYSQL_HOST` etc.) worth
  reusing when that source gets built, rather than reinventing it.
