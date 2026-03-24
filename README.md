# EIPS-TT - Preferential Crawler (PA1)

Clean default setup:
- Python in local `.venv`
- PostgreSQL in Docker (`docker compose`)
- Optional devcontainer (not required)

## Recommended One-Command Setup

```bash
bash scripts/bootstrap.sh
```

Safe to rerun after pulling new changes (it refreshes `.venv` packages, starts DB, and ensures base migration exists).

## Manual Setup (Docker Compose + .venv)

```bash
# 1) Start PostgreSQL
docker compose up -d db

# 2) Create and use Python virtual environment
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3) Initialize DB schema once (if needed)
docker compose exec -T db psql -U postgres -d postgres -f /docker-entrypoint-initdb.d/0_initial_crawldb.sql
```

Or use the provided bootstrap script instead:

```bash
bash scripts/bootstrap.sh
```

## Database Connection

Use this from your DB client/tool:

`postgresql://postgres:postgres@localhost:5432/postgres`

Credentials are local to your Docker Compose stack (`docker-compose.yml`), not shared externally.

## Test Python ↔ PostgreSQL

```bash
source .venv/bin/activate
python pa1/crawler/src/db/pg_connect.py
```

## VS Code

- Workspace default interpreter: `${workspaceFolder}/.venv/bin/python`
- If it does not auto-pick, run `Python: Select Interpreter` and choose `.venv/bin/python`.
- The old `.code-workspace` file was removed to avoid devcontainer/workspace nesting issues.

## Useful Commands

```bash
# Recreate/refresh Python env only
bash scripts/venv.sh

# Reset DB schema
bash scripts/reset-db.sh

# DB logs
docker compose logs -f db
```

## Optional Devcontainer

See `.devcontainer/NOTE.md`.
