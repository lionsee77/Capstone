# Taxonomy audit

The audit is read-only and prints its findings in taxonomy_audit.ipynb. 

Run the notebook from the backend directory, or run:

    docker compose --profile tools run --rm inspector python audit/audit_taxonomy.py

Near-duplicate and semantic-overlap checks are deliberately excluded.
