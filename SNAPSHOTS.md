# Taxonomy snapshots

Each enrichment round ends in a **snapshot**: a self-contained folder holding one
complete version of the taxonomy. Two are included, under
`backend/taxonomy_pipeline/integrations/`:

| Snapshot | What it is | Built on | Rows added | Categories / specialities / tags |
|---|---|---|---|---|
| *(original)* | The SenseiGigs taxonomy as supplied (`backend/sql-files-nus/`) | — | — | 110 / 593 / 676 |
| `judged-1015` | Original **+ SkillsFuture** (15 Oct 2026). "Judged" means ambiguous matches were settled by the LLM judge | the original | +39 / +953 / +1,248 | 149 / 1,546 / 1,924 |
| `onet-1031` | Original + SkillsFuture **+ O\*NET** software skills (31 Oct 2026). The newest, most enriched version | `judged-1015` | +0 / +147 / +144 | 149 / 1,693 / 2,068 |

Each folder contains:

- `baseline/`: everything the snapshot was built on. These are the original dumps, plus,
  for `onet-1031`, the `judged-1015` additions.
- `additions.sql`: only the rows this round added. It is insert-only and never updates
  or deletes a row.
- `enriched.yaml`: the order the files load in, used to score the snapshot.
- `manifest.json`: what was added, the IDs assigned and the review decisions used.

New rounds in the dashboard can build on either snapshot, and tag lookup answers
from the newest one (`onet-1031`).

## Load the enriched database (`onet-1031`) into MySQL

This rebuilds the fully enriched taxonomy in a local MySQL database for inspection.
It needs Docker Desktop. Run every command from `backend/`.

1. **Start MySQL** (the first start also imports the original taxonomy into `greygigz_beta`):

   ```bash
   cd backend
   docker compose up -d --build
   ```

2. **Load the snapshot** into the database `taxonomy_latest`:

   ```bash
   docker compose --profile tools run --rm loader onet-1031
   ```

   It prints each table's row count. Expect 4 services, 149 categories, 1,693
   specialities, 2,068 tags and 2,878 speciality–tag links. To load a different
   version, use its name instead (`loader judged-1015`). To keep several versions
   side by side, give each its own database:

   ```bash
   docker compose --profile tools run --rm -e TARGET_DATABASE=taxonomy_onet loader onet-1031
   ```

3. **Browse it** in Adminer at <http://localhost:8080>. Use system `MySQL`, server
   `db`, user `root`, password `localrootpassword` and database `taxonomy_latest`.
   You can also connect any MySQL client to `localhost:3307`.

The loader **drops and rebuilds** the target database each time, so it always equals
exactly one snapshot. It refuses to touch `greygigz_beta`, the original import. Run
it with no name to list the available snapshots.

### Without Docker

Create an empty database and import the files in this order, as `enriched.yaml`
lists them:

```bash
cd backend/taxonomy_pipeline/integrations/onet-1031
for f in baseline/services.sql baseline/categories.sql baseline/specialities.sql \
         baseline/tags.sql baseline/speciality_tags.sql baseline/additions.sql additions.sql; do
  mysql -u <user> -p <empty_database> < "$f"
done
```

Never load files from two snapshot folders into one database. `onet-1031` already
contains every `judged-1015` row.

## Applying the enrichment to the live database

Don't rebuild the live database from a snapshot. Instead:

1. Back up the live database.
2. Apply `judged-1015/additions.sql`, then `onet-1031/additions.sql`. Apply each
   once, in that order.
3. Before each one, check that the database still matches the baseline snapshot
   named in that file's header. The new rows' IDs continue from that snapshot's
   highest IDs, so a database that has changed since would clash.
4. Spot-check the new rows against the IDs listed in each round's `manifest.json`.
