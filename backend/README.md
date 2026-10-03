# Senseigigs local database

This backend runs the supplied SQL dumps in a local MySQL 8 container. It also
provides Adminer for visual browsing and a Python/MySQL taxonomy audit.

## Project structure

```text
backend/
├── sql-files-nus/        # Original SQL dumps
├── skillsfuture/         # SkillsFuture Excel sources and taxonomy generator
│   ├── generate_skillsfuture_taxonomy.py
│   ├── classify_unmapped_skills.py
│   ├── validate_skillsfuture_taxonomy.py
│   ├── validate_classification_proposals.py
│   ├── AI_TAXONOMY_PIPELINE_PLAN.md
│   └── *.xlsx              # SkillsFuture source workbooks
├── audit/                # Taxonomy audit module and report notebook
├── taxonomy_pipeline/    # Taxonomy quality scoring (CLI + FastAPI) - own README, own deps
├── compose.yaml          # MySQL, Adminer and audit services
├── Dockerfile            # MySQL image and ordered imports
├── Dockerfile.python     # Python audit image
└── requirements.txt      # Python MySQL connector
```

## Generate a SkillsFuture taxonomy

The `skillsfuture/` folder contains the three SkillsFuture Excel workbooks and
`generate_skillsfuture_taxonomy.py`. From this directory, install the Python
dependencies and run:

```bash
pip install -r requirements.txt
python skillsfuture/generate_skillsfuture_taxonomy.py
```

This reads the workbooks and writes `skillsfuture/skillsfuture_taxonomy.sql`.
It does not connect to MySQL or modify the existing database. The generated SQL
creates parallel `sf_categories`, `sf_specialities`, `sf_tags`, and
`sf_speciality_tags` tables, using the existing `services` table. Review the
generated file before importing it. Check it first with:

```bash
python skillsfuture/validate_skillsfuture_taxonomy.py
```

The validator is also read-only: it checks row shape, duplicate IDs/names,
duplicate speciality/tag links, broken references, dead-end categories,
specialities without tags, and unused tags.
Unused tags are expected inputs for the Case B classifier and should not be
treated as a reason to block that workflow.

The generated SQL uses parallel `sf_` tables so the original taxonomy is not
overwritten:

```text
services → sf_categories → sf_specialities → sf_speciality_tags → sf_tags
```

To create AI classification proposals for unused tags, put the OpenAI settings
in `backend/.env` and run `python skillsfuture/classify_unmapped_skills.py`.
The script loads `.env` automatically and writes pending JSON and CSV review
files only; it does not modify MySQL or generate executable SQL.

Create the local environment file from the template:

```bash
cp .env.example .env
```

Set at least:

```env
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
```

The classifier supports `--batch-size`, `--model`, and `--debug-response`.
For example:

```bash
python skillsfuture/classify_unmapped_skills.py --batch-size 5
```

The classifier maps generated tag IDs back to the source workbook skill IDs,
so skill descriptions are included in the model prompt. It checkpoints completed
batches in a hidden JSON file and resumes automatically after an interrupted
run. A successful run removes the checkpoint.

By default, the classifier reads `../taxonomy_pipeline/report.json` and
classifies only Case B tags: entries marked `ADD_CANDIDATE` with
`parent_status=NO_PARENT`. This avoids sending saturation duplicates or
`REVIEW_AMBIGUOUS` entries to the paid classifier. Tags with
`ADD_CANDIDATE` and `HAS_PARENT` belong to the separate Case A workflow.

Use `--report path/to/report.json` to select a different report. If the report
is unavailable, the classifier falls back to unused tags from the generated SQL;
this fallback does not perform saturation duplicate filtering.

Outputs are written to `skillsfuture/classification_proposals.json` and
`skillsfuture/classification_proposals.csv`. Both outputs use the fields in
`classification_output.schema.json`; the JSON output is validated against the
schema before it is written. Each record also includes an `action`:
`assign_existing`, `propose_new_speciality`, or `propose_new_category`.
Review and approve proposals before adding an approval-to-SQL step.

Validate proposals against the hierarchy with:

```bash
python skillsfuture/validate_classification_proposals.py
```

This creates annotated JSON and CSV files. It first validates the output against
`classification_output.schema.json`, then checks tag identity, duplicates,
services, and category/speciality relationships. Unknown categories or
specialities are reported as warnings for human review. Invalid records are
marked `needs_review`. The validated files are written as
`classification_proposals_validated.json` and
`classification_proposals_validated.csv` by default.

## Score taxonomy quality

`taxonomy_pipeline/` scores integrity, breadth, depth, and saturation for a
taxonomy - e.g. to compare the original taxonomy above against the generated
SkillsFuture one. It's a separate Python package with its own dependencies
(FastAPI, rapidfuzz, ...) and reads directly from `.sql` dump files, so it
needs neither the running database nor this folder's `requirements.txt`. See
[`taxonomy_pipeline/README.md`](taxonomy_pipeline/README.md) for setup and
usage.

## Start the database

1. Open Docker Desktop and wait until it reports that Docker is running.
2. From the `backend` directory, run:

   ```bash
   docker compose up -d --build
   ```

The SQL files are imported automatically the first time the database volume is
created. Later starts reuse the same database.

The host port is `3307` to avoid conflicting with a MySQL installation already
using the standard port `3306` on this Mac.

## View the database in a browser

Open <http://localhost:8080> and sign in with:

- System: `MySQL`
- Server: `db`
- Username: `senseigigs`
- Password: `localpassword`
- Database: `greygigz_beta`

These are local development credentials only. Change them through a `.env`
file if required; `.env` is excluded from version control.

## Audit through Python

Run the containerized read-only audit:

```bash
docker compose --profile tools run --rm inspector
```

Alternatively, run it directly from a local Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python audit/taxonomy_audit.py
```

## Stop or reset

Stop the services without deleting data:

```bash
docker compose down
```

To deliberately delete the local database and re-import all SQL files:

```bash
docker compose down --volumes
docker compose up -d --build
```

The reset command permanently removes only the Docker volume belonging to this
project. Do not use it if you need to keep local changes made in Adminer.

# Schema Structure

services
id=1, name=Industry
       │
       │ categories.service_id=1
       ▼
categories
id=1, name=Banking
       │
       │ specialities.category_id=1
       ▼
specialities
id=4, name=AML/KYC Program Design
       │
       │ speciality_tags.speciality_id=4
       ▼
speciality_tags
(4,8)  (4,9)  (4,10)  (4,11)
   │      │       │       │
   ▼      ▼       ▼       ▼
 design  aml   program    kyc
