# Engineering conventions

Conventions for this repo, read by both AI coding agents and contributors.
Modeled on `backend/taxonomy_pipeline/`, the most mature subproject here —
new work should follow the same shape unless there's a good reason not to.

## Project layout

- Each backend subproject is self-contained under `backend/<name>/`, with
  its own `pyproject.toml`, `src/<package>/` (src layout, not a flat
  package), and `tests/`.
- Favor a pluggable-registry pattern for anything that has multiple
  interchangeable implementations (data sources, metrics, etc.): define a
  small ABC/protocol in a `base.py`, put each implementation in its own
  module, and register instances in one `registry.py`. Adding a new one
  should mean "add a file + one registry line," not touching call sites.
- Keep one implementation behind both the CLI and the API where both exist
  (e.g. `pipeline.run_pipeline`) - no parallel logic paths for the same
  operation.
- Shared result/data shapes (what gets printed, persisted, and returned
  over HTTP) live in one `models.py` using Pydantic - one shape everywhere,
  not a CLI-specific and an API-specific version.

## Python style

- Target Python 3.11+, `from __future__ import annotations` at the top of
  every module, and modern built-in generics (`dict[str, Any]`, `X | None`)
  over `typing.Dict`/`Optional`.
- Type-hint function signatures, including return types.
- Module and class docstrings explain *why* a thing exists and how it's
  meant to be extended, not just what it does (see `metrics/base.py`,
  `models.py` for the pattern).
- Lint with `ruff`, `select = ["E", "F", "I", "UP", "B"]`, `line-length =
  120`. Run `ruff check .` clean before opening a PR.

## Tests

- `pytest`, one `test_<module>.py` per source module, plus `conftest.py`
  and a `fixtures/` dir for shared setup.
- Name test functions after the behavior being verified in plain language
  (`test_runs_discovery_curve_against_a_provided_corpus`), not
  `test_case_1`/generic names.
- New logic ships with tests in the same PR - untested code paths aren't
  considered done.

## Dependencies

- Declare runtime deps with capped ranges (`>=x,<y`) in `pyproject.toml`,
  dev-only tools (`pytest`, `httpx`, `ruff`) under
  `[project.optional-dependencies] dev`. Install with `pip install -e
  ".[dev]"`.

## CI

- One job per backend subproject in `.github/workflows/ci.yml`, scoped
  with `defaults.run.working-directory`, running (at minimum) `ruff check
  .` then `pytest -q`. A subproject without real lint/tests yet gets an
  explicit `TODO` placeholder step rather than a silently-skipped check.

## Git workflow

- Branch names: `<type>/<short-kebab-description>` - `feat/...`, `fix/...`,
  `chore/...`, matching the commit prefix below.
- Commit subjects use a conventional-commit-style prefix: `feat:`, `fix:`,
  `chore:`, `ci:`, `docs:`, `init:`. Keep the subject in plain imperative
  language after the prefix.
- Flow is feature branch -> `dev` -> `main`. Branch from `dev` (the default
  branch) and open PRs against `dev`; `main` only receives `dev` -> `main`
  promotion PRs. Both branches carry the same protections and required
  checks.
- Land work via PR + merge commit (`Merge pull request #N ...`), not direct
  pushes to `dev` or `main`.

## Docs

- Every subproject with a CLI/API surface gets a `README.md` covering
  setup, how to run it, and its directory structure as an annotated tree -
  don't rely on the code alone to onboard the next contributor.
- Read a subproject's `README.md` before changing that subproject.
- If a change alters behaviour, config params, CLI flags, or report fields,
  update that subproject's README in the same PR - a stale README is a bug.
