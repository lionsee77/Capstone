# SenseiGigs Taxonomy Pipeline

A repeatable, reviewable way to add skills from external sources (SkillsFuture,
O\*NET, ESCO, ...) to the SenseiGigs taxonomy. It scores the taxonomy's quality
before and after each addition, and comes with an admin dashboard. It never
writes to a production database: every round produces a versioned snapshot and an
insert-only `additions.sql` for a person to apply.

This file gets the project running. Each part has its own README with the full
detail; see [Where to read next](#where-to-read-next).

## Layout

```text
.
├── backend/
│   ├── taxonomy_pipeline/   # the pipeline: Python package, CLI and FastAPI service (own README)
│   │   └── Dockerfile       # pipeline API image (built by the root compose.yaml)
│   ├── sql-files-nus/       # the original SenseiGigs taxonomy dumps (confidential)
│   ├── skillsfuture/        # SkillsFuture workbooks and their generator script
│   ├── onet/, esco/         # further sources, each with a mapping for import-source
│   ├── reference_data/      # SSIC 2025 and SSOC 2024, used by the breadth metric
│   ├── loader/, compose.yaml, Dockerfile   # optional local MySQL and snapshot loader
│   └── .env.example         # pipeline API settings (copy to backend/.env)
├── frontend/                # Next.js admin dashboard over the pipeline API (own README)
│   ├── Dockerfile           # dashboard image (built by the root compose.yaml)
│   └── .env.example         # dashboard settings (copy to frontend/.env.local)
├── compose.yaml             # API + dashboard in Docker (see "Run with Docker instead")
└── .github/workflows/ci.yml # lint and tests for both parts
```

The dashboard has no logic of its own. Every number it shows comes from the
pipeline API, so the API must be running first.

## Prerequisites

- Python 3.11 or newer
- [bun](https://bun.sh) 1.2 or newer for the dashboard (Node with `npx` also works; see below)
- Docker, only to browse a snapshot in MySQL
- An OpenAI API key, only for the optional LLM steps (judge, Case B, group naming)

## One-time setup

**1. Install the pipeline** in a virtual environment:

```bash
cd backend/taxonomy_pipeline
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,semantic,llm]"                  # semantic = sentence model, llm = OpenAI steps
taxonomy-pipeline --help                              # checks the command is installed
```

The first run that matches by meaning downloads `all-MiniLM-L6-v2` (about 90 MB);
after that it works offline.

**2. Generate the SkillsFuture files** if `backend/skillsfuture/` has no
generated tables yet (they are git-ignored):

```bash
cd ..                                   # backend/
pip install -r requirements.txt
python skillsfuture/generate_skillsfuture_taxonomy.py
```

**3. Choose an API token** and put the same value in both env files. The API
refuses every request without one, and it treats the `change-me` placeholder as
no token.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
cp backend/.env.example backend/.env              # set TAXONOMY_API_TOKEN=<the value>
cp frontend/.env.example frontend/.env.local      # set TAXONOMY_API_TOKEN=<the same value>
```

`backend/.env` also takes `OPENAI_API_KEY` and `TAXONOMY_ALLOW_LLM=true` if you
want the dashboard to offer LLM steps. In `frontend/.env.local`, set
`PIPELINE_ALLOW_RUN_CREATION=true` to start rounds and record review decisions
from the dashboard; it is read-only otherwise. Never commit either file.

**4. Install the dashboard:**

```bash
cd frontend
bun install
```

## Start it

Use two terminals, starting the API first:

```bash
# Terminal 1: pipeline API on http://localhost:8000
cd backend/taxonomy_pipeline && source .venv/bin/activate
taxonomy-pipeline serve --reload

# Terminal 2: dashboard on http://localhost:3000
cd frontend
bun dev
```

Without bun, run `npx next dev --hostname 127.0.0.1` in `frontend/` instead.

Open http://localhost:3000. The sidebar shows "Pipeline API connected" when the
two can talk. If the dashboard reports the API as unreachable or returning 401,
check that both env files hold the same token and restart both. The API's own
reference is at http://localhost:8000/docs (use **Authorize** to supply the token).

The dashboard listens on `127.0.0.1` only. It has no login and can start rounds
that spend LLM credits, so keep both parts off shared networks.

A fresh copy has no run history or snapshots, so the run and Improvement pages
stay empty until a first scoring run or round completes. To produce one:

```bash
cd backend/taxonomy_pipeline
taxonomy-pipeline run --config configs/baseline_v1.yaml --output baseline.json.gz   # scores the original taxonomy
```

## Run the checks

These are the checks CI runs on every pull request. Run them before opening one.

```bash
# Pipeline
cd backend/taxonomy_pipeline
python -m ruff check .
python -m pytest -q

# Dashboard
cd frontend
bun run lint
bun run typecheck
bun run test
bun run build
```

## Run with Docker instead

This needs only Docker Desktop: no Python, bun or virtual environment. It works the same on macOS,
Windows and Linux. Run from the repository root:

```bash
cp backend/.env.example backend/.env   # set TAXONOMY_API_TOKEN; OPENAI_API_KEY only for LLM steps
docker compose up --build              # dashboard http://localhost:3000, API http://localhost:8000/docs
```

The dashboard gets the token from `backend/.env` too, so `frontend/.env.local` isn't used. To let
the dashboard start and change rounds, add `PIPELINE_ALLOW_RUN_CREATION=true` to `backend/.env`.
Both ports listen on localhost only.

`backend/` is mounted into the API container, so snapshots, rounds and saved runs are the same files
the non-Docker setup uses. The sentence model is built into the image, so nothing downloads at run time.
Paths saved by a run inside the container start with `/app/backend`, so use either Docker or a local
install for a given round, not both.

```bash
docker compose --profile test run --rm backend-tests     # pytest
docker compose --profile test run --rm dashboard-tests   # vitest
docker compose run --rm api taxonomy-pipeline run --config configs/baseline_v1.yaml   # any CLI command
docker compose down                                      # stop
```

The first build takes a few minutes and uses about 2.5 GB. Python changes take effect after
`docker compose restart api`, because the source is mounted. Dashboard changes, or new Python
dependencies, need `docker compose up --build`. MySQL is separate and optional: see [backend/README.md](backend/README.md).

## Where to read next

| Read | For |
|---|---|
| [`backend/taxonomy_pipeline/README.md`](backend/taxonomy_pipeline/README.md) | Every command, metric, setting and API route: start with "Project structure", "Setup", "Run it as a service" and "Classification workflow" |
| [`frontend/README.md`](frontend/README.md) | Dashboard pages, settings, security and conventions |
| [`backend/README.md`](backend/README.md) | Local MySQL, SkillsFuture generation and loading a snapshot into MySQL |
| [`backend/onet/README.md`](backend/onet/README.md), [`backend/esco/README.md`](backend/esco/README.md) | Worked examples of preparing a new source |
| [`AGENTS.md`](AGENTS.md) | Engineering conventions: layout, style, tests, branches and commits |
