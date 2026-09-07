# Senseigigs local database

This backend runs the supplied SQL dumps in a local MySQL 8 container. It also
provides Adminer for visual browsing and a Python/MySQL inspection script.

## Project structure

```text
backend/
├── sql-files-nus/        # Original SQL dumps
├── compose.yaml          # MySQL, Adminer and inspector services
├── Dockerfile            # MySQL image and ordered imports
├── Dockerfile.python     # Python inspector image
├── inspect_database.py   # Schema and integrity report
└── requirements.txt      # Python MySQL connector
```

## Start the database

1. Open Docker Desktop and wait until it reports that Docker is running.
2. From the `backend` directory, run:

   ```bash
   docker compose up -d --build
   ```

The SQL files are imported automatically the first time the database volume is
created. Later starts reuse the same database.

The host port is `3307` to avoid conflicting with a MySQL installation already
using the standard port `3306` on this Mac.

## View the database in a browser

Open <http://localhost:8080> and sign in with:

- System: `MySQL`
- Server: `db`
- Username: `senseigigs`
- Password: `localpassword`
- Database: `greygigz_beta`

These are local development credentials only. Change them through a `.env`
file if required; `.env` is excluded from version control.

## Inspect through Python

Run the containerized Python inspector:

```bash
docker compose --profile tools run --rm inspector
```

Alternatively, run it directly from a local Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python inspect_database.py
```

## Stop or reset

Stop the services without deleting data:

```bash
docker compose down
```

To deliberately delete the local database and re-import all SQL files:

```bash
docker compose down --volumes
docker compose up -d --build
```

The reset command permanently removes only the Docker volume belonging to this
project. Do not use it if you need to keep local changes made in Adminer.

services
id=1, name=Industry
       │
       │ categories.service_id=1
       ▼
categories
id=1, name=Banking
       │
       │ specialities.category_id=1
       ▼
specialities
id=4, name=AML/KYC Program Design
       │
       │ speciality_tags.speciality_id=4
       ▼
speciality_tags
(4,8)  (4,9)  (4,10)  (4,11)
   │      │       │       │
   ▼      ▼       ▼       ▼
 design  aml   program    kyc
