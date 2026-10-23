# Taxonomy dashboard

Web UI for the taxonomy validation pipeline (`backend/taxonomy_pipeline/`):
visualise the taxonomy, browse validation run history, and plug in new data
sources that are validated against the existing taxonomy through the
pipeline's FastAPI endpoints.

The dashboard has no backend logic of its own. Everything it shows comes from
the pipeline API (`GET /metrics`, `POST /runs`, `GET /runs`, `GET /runs/{id}`,
`GET /taxonomy/search`, `GET /taxonomy/{level}/{id}`), so the CLI, the API,
and this UI all agree on what a run is.

**Stack:** Next.js 16 (App Router) · TanStack Query (server state) · zod (API
boundary validation) · zustand (client state) · shadcn/ui on Base UI + Tailwind v4 ·
Vitest · bun.

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
- The proxy forwards only `GET /metrics`, `GET /runs`, `GET /runs/{id}`,
  `GET /taxonomy/search`, `GET /taxonomy/{level}/{id}` and, when
  `PIPELINE_ALLOW_RUN_CREATION=true`, `POST /runs`. The allowlist lives in
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
│   │   ├── runs/                     #   run history (polls while runs are active, "load older")
│   │   │   └── [runId]/              #   one run's report; polls until done/failed
│   │   └── sources/new/              #   new data-source integration (placeholder)
│   ├── components/
│   │   ├── ui/                       #   shadcn components - generated, add via the CLI
│   │   └── layout/                   #   app shell pieces
│   ├── lib/
│   │   ├── api/
│   │   │   ├── schemas.ts            #   zod mirrors of models.py / config.py
│   │   │   ├── client.ts             #   fetch wrapper; parses every response with zod
│   │   │   ├── proxy.ts              #   which pipeline endpoints the proxy forwards
│   │   │   └── queries.ts            #   TanStack Query hooks + query keys
│   │   ├── metric-labels.ts          #   labels repeated metrics by their differing params
│   │   └── utils.ts                  #   `cn` class-name helper (shadcn)
│   └── stores/
│       └── source-draft-store.ts     #   zustand: datasets + metrics for a new source run
├── tests/                            # Vitest, one <module>.test.ts per source module
│   └── fixtures/                     #   API-shaped payloads
├── components.json                   # shadcn config
└── next.config.ts
```

## Conventions

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
- **New data source flow:** the store, schema, and `useCreateRun` mutation are
  ready, but the form isn't built yet. Dataset paths must be absolute
  *server-side* paths, so uploading a file from the browser would need a new
  upload endpoint.
- **Run reports** render each metric's `details` as raw JSON for now. Saturation
  curves and breadth coverage still need proper charts.
