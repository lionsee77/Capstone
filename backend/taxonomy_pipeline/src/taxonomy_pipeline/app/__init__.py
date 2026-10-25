"""Use cases: one function per CLI command, the single implementation behind both adapters.

`cli.py` and `api/` only translate their inputs (flags, HTTP bodies) into a
call here and its result back into output (text and exit codes, JSON and
status codes). The checks, file writes and warnings a command involves live in
the use case, so a future API endpoint for the same operation (e.g.
dashboard-driven review) calls the same function rather than copying the CLI.
Use cases raise `ValueError` or `OSError` for anything the caller should report
as a failed command; warnings come back on the result instead of being printed.

- `scoring.py`: `run`, `check-comparable`, report I/O, dataset loading.
- `enrichment.py`: `case-a`, `case-b`, `merge-preview`, `review-proposals`,
  `bulk-decide`, `integrate`.
"""
