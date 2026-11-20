# Taxonomy audit

`taxonomy_audit.py` is the single source of truth for the read-only database
inventory and taxonomy quality checks.

From the `backend` directory, run it in Docker:

```bash
docker compose --profile tools run --rm inspector
```

For a local Python environment connected to the database on port 3307:

```bash
python audit/taxonomy_audit.py
python audit/taxonomy_audit.py --output taxonomy-report.md
```

Connection settings can be overridden with `MYSQL_HOST`, `MYSQL_PORT`,
`MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD`.

The audit covers schema safeguards, hierarchy integrity, names, duplicates,
formatting, status and timestamp consistency, unused records, metadata coverage,
and taxonomy distributions. Near-duplicate and semantic-overlap checks remain
excluded because they require domain judgement.
