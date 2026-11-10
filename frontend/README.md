# Taxonomy dashboard

Web UI for the taxonomy validation pipeline (`backend/taxonomy_pipeline/`):
visualise the taxonomy, browse validation run history, and plug in new data
sources that are validated against the existing taxonomy through the
pipeline's FastAPI endpoints.

The dashboard has no backend logic of its own. Everything it shows comes from
the pipeline API (`GET /metrics`, `POST /runs`, `GET /runs`, `GET /runs/{id}`,
`GET /runs/{before}/compare/{after}`, `GET /lineage`, `GET /lookup`,
`GET /taxonomy/search`, `GET /taxonomy/{level}/{id}`), so the CLI, the API, and this UI all agree on
what a run is - and no metric is computed here: every value, change and
verdict on screen is the API's.

## Pages

- **New enrichment round** (`/sources/new`) - start a round on a chosen snapshot or the original
  taxonomy (the newest snapshot is the default), drop its
  file or folder, map its columns and set its options, then import and run it. Columns come
  from the uploaded file, with sample values, so nothing is typed from memory. Importing shows
  counts and warnings, and shows any mapping error from the API in place. LLM steps can only be
  ticked when the server allows them, and ticking one says it will spend credits.
- **Enrichment rounds** (`/rounds`, `/rounds/{id}`) - one new source brought into
  the taxonomy: each stage's status, summary and error, updating while it runs, with
  buttons to run, resume or remove the round and the generated config. Reads `/rounds`.
  Once a merge preview exists, a review panel lists each held tag with the reason, its
  proposed placements and closest existing matches. A reviewer creates, reuses, excludes or
  defers it, one at a time or across a selected page, or drafts the clear-cut ones first.
  Reuse never picks a tag for the reviewer: each option says whether it matched by spelling or
  meaning, because the closest spelling is often a different concept.
  Decisions are saved at once, then applied together, because `merge-preview` rebuilds the
  whole preview to check them (about 25 seconds for O*NET). Then "Generate enriched snapshot"
  integrates and scores the round, and links to its before-and-after comparison. That one
  run scores both versions, so the round also appears on the Improvement page as a two-stage
  comparison. Earlier stages show as not scored until a whole-chain run covers them.
- **Tag lookup** (`/lookup`) - check whether a term already exists before proposing
  it. It reads `GET /lookup`, so it needs no run: the result names the snapshot it
  checked (the newest one under `integrations/`). Shown: a verdict, then exact and
  close matches with level, how each matched, and where it sits. Matching by meaning
  is opt-in, because the first such search loads a language model on the server.
  Parents are plain text rather than links, since concept pages need a run ID.
- **Improvement** (`/improvement`) - how the taxonomy grew round by round
  (Baseline 1 -> 2 -> 3 ...). It reads `GET /lineage` for the chain of
  enrichment rounds and the best run that scored them (most stages, then judge
  on, then newest; a picker offers the others), then compares each stage with
  the next and the first with the last through `/runs/{id}/compare/{id}`.
  Shown: a card per stage (source added, size, rows added), a like-for-like
  banner, tiles for the biggest rate changes, and a section per metric with a
  trend chart of its rates and a table of every measure (value per stage,
  step changes, sparkline, verdict). Saturation gets one card per incoming
  source, so "how much of each source is still missing" is visible per round.
  Nothing is hard-coded: a new round, source, metric or run appears from the
  API alone.
- **Compare runs** (`/compare`) - any two finished runs, or two taxonomies of
  one run, side by side with changes and verdicts. The picks live in the URL
  (`?before=&before_subject=&after=&after_subject=`), so a comparison can be
  linked; the Improvement page links its first-vs-latest view here.
- **Run detail** (`/runs/{id}`) - one run as readable values: a tab per
  taxonomy it scored, each metric's rates as meters and counts as figures.
  Values come from comparing the taxonomy with itself, so labels, units and
  better directions are the API's. Raw details load only on "Show raw
  details": a judged saturation run stores every concept it reviewed (tens of
  megabytes), so pages otherwise read runs with `?details=false`.

Charts are plain SVG (`src/components/viz/`), drawn to the dataviz skill's
rules: the validated categorical slots and the reserved good/bad status pair
are CSS variables in `globals.css` (light and dark), lines are 2px with
direct end labels and a legend, every chart has a hover tooltip, and a
verdict always carries an arrow and a word, never colour alone.

**Stack:** Next.js 16 (App Router) · TanStack Query (server state) · zod (API
boundary validation) · zustand (client state) · shadcn/ui on Base UI + Tailwind v4 ·
Vitest · bun.

PB09 audit response contracts (`changeEventSchema`, `changeHistorySchema`) mirror
the backend's `audit/models.py`, including snapshot-scoped concept IDs, proposal
references and review-versus-snapshot scope. Additions' `linked_reviews` retain the original decision, reviewer,
timestamp and event references separately from the integration actor. Older events
default to an empty list; unavailable legacy reviewer/time values are null.
The review event schema accepts the self-reported `actor` field (default `unspecified` for old records).
Audit history is currently available through the backend `GET /changes` and CLI;
no dashboard history screen or proxy route is added here.

## Setup

```bash
cd frontend
bun install
cp .env.example .env.local   # optional, defaults to http://localhost:8000
```

| Env var | Default | Meaning |
|---|---|---|
| `TAXONOMY_API_URL` | `http://localhost:8000` | Where `taxonomy-pipeline serve` is running |
| `TAXONOMY_API_TOKEN` | *(none)* | The token of the pipeline API this dashboard calls (the same value as that API's `backend/.env`). The proxy adds it server-side, so it never reaches the browser |
| `PIPELINE_ALLOW_RUN_CREATION` | `false` | Allow the dashboard to start runs (`POST /runs`). Enable only in a trusted local environment (see [Security](#security)) |

## Run it

Start the pipeline API first (see `backend/taxonomy_pipeline/README.md`), then:

```bash
# terminal 1
cd backend/taxonomy_pipeline && taxonomy-pipeline serve --reload

# terminal 2
cd frontend && bun dev        # http://localhost:3000
```

The browser calls `/api/pipeline/*`. A route handler
(`src/app/api/pipeline/[...path]/route.ts`) forwards only the endpoints the
dashboard uses to `TAXONOMY_API_URL`. Requests stay same-origin, so the FastAPI
app needs no CORS config.

`bun dev` and `bun start` listen on `127.0.0.1` only. The dashboard has no login, and
it can start rounds that spend LLM credits, so don't open it to the network without one.

## Security

The pipeline API requires a token, and `POST /runs` only reads dataset files
inside the server's `TAXONOMY_DATA_ROOTS` (see "Run it as a service" in
`backend/taxonomy_pipeline/README.md`, including who needs which token). The proxy adds the token server-side
(`upstreamHeaders` in `src/lib/api/proxy.ts`), so the browser never sees it.
That also means **anyone who can open the dashboard acts with the token**,
since the dashboard itself has no login yet:

- Run the dashboard only on localhost or a trusted network until it has its own
  login. The API itself can now be exposed to the dashboard's server without
  being open to everyone.
- The proxy forwards only `GET /metrics`, `GET /runs`, `GET /runs/{id}`
  (with `details=false` only), `GET /runs/{id}/compare/{id}` (with
  `before_subject`/`after_subject` only), `GET /lineage`, the read routes of `/rounds`,
  `GET /lookup` (with `q`, `level` and `semantic` only),
  `GET /taxonomy/search`, `GET /taxonomy/{level}/{id}`,
  `GET /proposals`, `GET /proposals/{id}` and, when
  `PIPELINE_ALLOW_RUN_CREATION=true`, `POST /runs` and the routes that change a round. The allowlist lives in
  `src/lib/api/proxy.ts`. Add a route there, with a test, when the dashboard
  starts using a new endpoint. The two `/taxonomy` routes also require a
  valid `run_id` and `dataset` query param - a request missing either is
  dropped as 404 before it reaches the pipeline API.
- POSTs that pass the allowlist must also be same-origin JSON. Other content
  types get 415, and browser requests from another site get 403. Without this,
  a page on another site could start runs through a visitor's dashboard with a
  `text/plain` form post, which needs no CORS preflight.
  - `Sec-Fetch-Site` decides when the browser sends it, which every current
    browser does, including behind a reverse proxy that rewrites `Host`.
  - Older browsers fall back to comparing `Origin` with
    `X-Forwarded-Host` (or `Host`).
  - Non-browser clients such as curl send neither header and are allowed.
- The allowlist is checked first, so unknown paths are always 404 and
  disabled run creation is always 403, whatever the body.

## Project structure

```text
frontend/
├── src/
│   ├── app/                          # App Router routes
│   │   ├── api/pipeline/[...path]/   #   allowlisted proxy to the pipeline API
│   │   ├── layout.tsx                #   shell: sidebar nav (top bar on mobile) + Providers
│   │   ├── providers.tsx             #   QueryClientProvider (+ devtools)
│   │   ├── page.tsx                  #   overview: live metric catalogue
│   │   ├── taxonomy/                 #   search a run's dataset by name, optionally filtered by level
│   │   │   └── [level]/[conceptId]/  #   one concept's path(s), children and tags
│   │   ├── improvement/              #   Baseline 1 -> 2 -> 3: stages, highlights, per-metric trends (GET /lineage)
│   │   ├── compare/                  #   any two runs (or two taxonomies of one run) side by side
│   │   ├── runs/                     #   run history (polls while runs are active, "load older")
│   │   │   └── [runId]/              #   one run as meters and figures per taxonomy; polls until done/failed
│   │   └── sources/new/              #   new data-source integration (placeholder)
│   ├── components/
│   │   ├── ui/                       #   shadcn components - generated, add via the CLI
│   │   ├── layout/                   #   app shell: sidebar nav, API status, PageHeader / Section / Notice
│   │   ├── viz/                      #   SVG trend chart, sparkline, meter, verdict chip
│   │   ├── run-status-badge.tsx      #   run and metric status as a coloured dot and label
│   │   └── level-tag.tsx             #   a concept's level as a small mono tag
│   ├── lib/
│   │   ├── api/
│   │   │   ├── schemas.ts            #   zod mirrors of models.py / config.py
│   │   │   ├── client.ts             #   fetch wrapper; parses every response with zod
│   │   │   ├── proxy.ts              #   which pipeline endpoints the proxy forwards
│   │   │   └── queries.ts            #   TanStack Query hooks + query keys
│   │   ├── metric-labels.ts          #   labels repeated metrics by their differing params
│   │   ├── improvement.ts            #   lines stage comparisons up into trend rows (no metric logic)
│   │   ├── run-view.ts               #   which taxonomies a run scored
│   │   ├── format.ts                 #   values by unit (rate -> %, change -> pts), verdict wording, dates, short run ids
│   │   └── utils.ts                  #   `cn` class-name helper (shadcn)
│   └── stores/
│       └── source-draft-store.ts     #   zustand: datasets + metrics for a new source run
├── tests/                            # Vitest, one <module>.test.ts per source module
│   └── fixtures/                     #   API-shaped payloads
├── components.json                   # shadcn config
└── next.config.ts
```

## Conventions

Proposal screening includes a `suggested_action` (optional for older saved
reports). The shared `reviewDecisionSchema` mirrors backend tag and hierarchy
decisions: tag groups use `members`, hierarchy proposals use `proposal_id`, and
both use `create` / `exclude` / `reuse` / `defer`. `reviewDecisionEventSchema`
mirrors the local decision-log event.

For #58, `api.listProposals(mergeRun, { level, offset, limit })` and
`api.getProposal(mergeRun, proposalId)` read saved proposal evidence through the
allowlisted proxy. `proposalReviewReportSchema` mirrors the shared CLI/API
response: original proposed branches, all existing matches and scores, screening
settings and warnings, and the separate saved reviewer decision. The original
screening suggestion can differ from that decision. A resolved temporary node
does not establish approval or database insertion.

`mergeRun` is a directory name under the backend's `TAXONOMY_MERGE_ROOT`, not a
saturation run ID or an arbitrary path. Lists support category/speciality filters
and pagination (default 50, maximum 200); detail returns the same envelope with
one item. The proxy permits reads only. No dashboard approval controls or new
decision-submission flow are introduced; screen layout remains #54 follow-up.

- **Server state goes through TanStack Query, client state goes in zustand.**
  Components call the hooks in `lib/api/queries.ts`. They never call `fetch`
  directly. Zustand holds only UI state that doesn't exist on the server yet,
  such as a source config that hasn't been submitted.
- **`schemas.ts` mirrors the Pydantic models.** If you change
  `models.py` or `config.py` in the pipeline, update the matching zod schema in
  the same PR. `client.ts` parses every response, so drift fails loudly.
  Proposal screening uses singular levels (`category`, `speciality`) and can
  retain null IDs as evidence. Taxonomy API concepts use plural levels and require
  non-null IDs; keep their schema helpers separate.
- **shadcn components:** add with `bunx --bun shadcn@latest add <component>`.
  They land in `src/components/ui/`. Treat them as generated, and wrap them
  rather than editing them heavily.
- **Next.js 16 differs from older versions** (`params` is a Promise, global
  `PageProps`/`LayoutProps` route types, and more). Check
  `node_modules/next/dist/docs/` before relying on memory. See `AGENTS.md`.

## Development

```bash
bun run lint        # eslint
bun run typecheck   # next typegen + tsc
bun run test        # vitest
bun run build       # production build
```

CI runs all four. See the `frontend` job in `.github/workflows/ci.yml`.

## Known gaps

- **Taxonomy search:** no persistent "current taxonomy" exists yet - every
  search is scoped to one past run's dataset (picked via a run + dataset
  selector), re-read from its source files on every request rather than
  from a snapshot. So it's a live view of those files, not of that run: an
  edit to a dataset file changes results for the same run immediately,
  with no new run needed - the run is only how you pick which files.
- **Source shapes:** a round imports one flat table per source. Files from a dropped folder
  are stored flat under their own names, so a later file with the same name replaces an earlier one.
- **`source-draft-store`** was written for an earlier plan and isn't used by the new-round form.
- **Run reports** render each metric's `details` as raw JSON for now. Saturation
  curves and breadth coverage still need proper charts.
