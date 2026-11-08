"""Use cases: one function per CLI command or API route, the single implementation behind both adapters.

`cli.py` and `api/` only translate their inputs (flags, HTTP bodies) into a
call here and its result back into output (text and exit codes, JSON and
status codes). The checks, file writes and warnings a command involves live in
the use case, so a future API endpoint for the same operation (e.g.
dashboard-driven review) calls the same function rather than copying the CLI.
Use cases raise `ValueError` or `OSError` for anything the caller should report
as a failed command; warnings come back on the result instead of being printed.

- `scoring.py`: `run`, `check-comparable`, report I/O, dataset loading, and run
  history and the metric catalogue for `/metrics` and `/runs`.
- `enrichment.py`: `case-a`, `case-b`, `merge-preview`, `review-proposals`
  (also `/proposals`), `bulk-decide`, `integrate`.
- `browse.py`: taxonomy search and concept detail for `/taxonomy`.
- `rounds.py`: a whole enrichment round as tracked background stages (`/rounds`),
  calling the use cases above and leaving a person to review before it integrates.
- `lookup.py`: `lookup` (also `/lookup`): does a proposed term already exist?
- `sources.py`: `import-source` (flat source -> taxonomy dump, tag context and
  the next round's config).

Security checks (the API token, allowed data roots, allowed merge-run
directories) stay in `api/`: they decide whether a request may run at all.
"""
