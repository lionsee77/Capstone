# Taxonomy dashboard

Web UI for the taxonomy validation pipeline (`backend/taxonomy_pipeline/`):
visualise the taxonomy, browse validation run history, and plug in new data
sources that are validated against the existing taxonomy through the
pipeline's FastAPI endpoints.

The dashboard has no backend logic of its own. Everything it shows comes from
the pipeline API (`GET /metrics`, `POST /runs`, `GET /runs`, `GET /runs/{id}`),
so the CLI, the API, and this UI all agree on what a run is.

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

The pipeline API has **no authentication**, and `POST /runs` makes the server
read whatever absolute dataset paths the request names. Until the API has auth:

- Run the dashboard and the pipeline API only on localhost or a trusted network.
  Don't deploy either one publicly.
- The proxy forwards only `GET /metrics`, `GET /runs`, `GET /runs/{id}` and,
  when `PIPELINE_ALLOW_RUN_CREATION=true`, `POST /runs`. The allowlist lives in
  `src/lib/api/proxy.ts`. Add a route there, with a test, when the dashboard
  starts using a new endpoint.
- POSTs must be same-origin JSON. Other content types get 415, and browser
  requests from another site (by `Sec-Fetch-Site` / `Origin`) get 403.
  Without this, a page on another site could start runs through a visitor's
  dashboard with a `text/plain` form post, which needs no CORS preflight.
  Non-browser clients such as curl send neither header and are allowed. If the
  dashboard ever sits behind a reverse proxy that rewrites `Host`, revisit the
  `Origin` check in `checkWriteRequest`.

## Project structure

```text
frontend/
├── src/
│   ├── app/                          # App Router routes
│   │   ├── api/pipeline/[...path]/   #   allowlisted proxy to the pipeline API
│   │   ├── layout.tsx                #   shell: sidebar nav (top bar on mobile) + Providers
│   │   ├── providers.tsx             #   QueryClientProvider (+ devtools)
│   │   ├── page.tsx                  #   overview: live metric catalogue
│   │   ├── taxonomy/                 #   taxonomy tree view (placeholder, see Known gaps)
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

- **Taxonomy view:** the pipeline API has no endpoint yet that serves a loaded
  dataset's tree (services -> categories -> specialities -> tags). That
  endpoint is needed before this page can render anything.
- **New data source flow:** the store, schema, and `useCreateRun` mutation are
  ready, but the form isn't built yet. Dataset paths must be absolute
  *server-side* paths, so uploading a file from the browser would need a new
  upload endpoint.
- **Run reports** render each metric's `details` as raw JSON for now. Saturation
  curves and breadth coverage still need proper charts.
