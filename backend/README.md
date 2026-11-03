# Senseigigs local database

This backend runs the supplied SQL dumps in a local MySQL 8 container. It also
provides Adminer for visual browsing and a Python/MySQL taxonomy audit.

## Project structure

```text
backend/
├── sql-files-nus/        # Original SQL dumps
├── skillsfuture/         # SkillsFuture Excel sources and taxonomy generator (data prep)
│   ├── generate_skillsfuture_taxonomy.py
│   ├── validate_skillsfuture_taxonomy.py
│   ├── AI_TAXONOMY_PIPELINE_PLAN.md
│   └── *.xlsx              # SkillsFuture source workbooks
├── audit/                # Taxonomy audit module and report notebook
├── loader/               # load_snapshot.sh: rebuild a database from one pipeline snapshot
├── onet/                # O*NET source prepared for the pipeline's import-source
├── taxonomy_pipeline/    # Quality scoring + Case A/B classification and merge preview (CLI + FastAPI) - own README, own deps
├── compose.yaml          # MySQL, Adminer, audit and snapshot-loader services
├── Dockerfile            # MySQL image and ordered imports
├── Dockerfile.python     # Python audit image
└── requirements.txt      # Python deps for audit/ and skillsfuture/ data prep
```

The admin dashboard lives in [`../frontend/`](../frontend/README.md) and reads
everything from the `taxonomy_pipeline` API.

## Generate a SkillsFuture taxonomy

The `skillsfuture/` folder contains the three SkillsFuture Excel workbooks and
`generate_skillsfuture_taxonomy.py`. From this directory, install the Python
dependencies and run:

```bash
pip install -r requirements.txt
python skillsfuture/generate_skillsfuture_taxonomy.py
```

This reads the workbooks and writes `skillsfuture/skillsfuture_taxonomy.sql`.
The generator remains standalone and needs only the dependencies above.
A pipeline regression test compares its generated tag IDs and titles with
Case B's SkillsFuture context loader, including untitled rows and whitespace.
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

The generated SQL is the incoming SkillsFuture dataset for
`taxonomy_pipeline/`. Classifying its new tags into the original taxonomy -
Case A (tags with their own SkillsFuture category), Case B (LLM placement for
the rest), the merge preview that combines them, and the integrate step that
turns the reviewed result into an enriched taxonomy snapshot - runs from there
as `taxonomy-pipeline case-a | case-b | merge-preview | bulk-decide | integrate`; see
[Classification workflow](taxonomy_pipeline/README.md#classification-workflow).
Case B's OpenAI settings still go in `backend/.env` (copy `.env.example`).

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

## Load a pipeline snapshot

Each classification round ends with an `integrate` snapshot in
`taxonomy_pipeline/integrations/<name>/`. It holds everything that stage of the
taxonomy needs: the original dumps and every earlier round's `additions.sql`
in `baseline/`, plus that round's own `additions.sql`, in the order its
`enriched.yaml` lists. To see a stage in MySQL:

```bash
docker compose --profile tools run --rm loader onet-1031     # original + SkillsFuture + O*NET
docker compose --profile tools run --rm loader judged-1015   # original + SkillsFuture
docker compose --profile tools run --rm -e TARGET_DATABASE=taxonomy_r1 loader judged-1015   # another database
```

- It **drops and rebuilds** the target database (default `taxonomy_latest`)
  from that one folder, rather than applying `additions.sql` to whatever is
  there. The database then always equals exactly one snapshot, a round can't be
  applied twice or out of order, and going back a stage is loading an older
  snapshot. Never load files from two snapshot folders into one database: the
  later snapshot already contains the earlier one's rows.
- `greygigz_beta` (the original import) is never a valid target, nor are
  MySQL's own databases. It runs as root because it creates databases.
  Snapshot names may use letters, digits, `.`, `_` and `-`; target names
  letters, digits and `_`.
- The whole rebuild runs in one MySQL session holding a lock on the target, so
  a second loader started meanwhile stops without changing anything. If a file
  fails, the output says which one and that the target is now partial.
- It prints each table's row count (using the snapshot's own table names) and
  records the snapshot name in a `_snapshot` table. Browse it in Adminer as user `root` (password
  `MYSQL_ROOT_PASSWORD`, default `localrootpassword`), database
  `taxonomy_latest`.
- Run without a name, or with an unknown one, it lists the available snapshots.

This is for local inspection. The client's live database can't be rebuilt:
there, apply each round's `additions.sql` once, in order, and only while the
database still matches the snapshot named in that file's header.

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
