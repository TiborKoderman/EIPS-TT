# Migrations and Requirements Workflow

## Recommended setup

Use:

```bash
bash scripts/venv-restore.sh
bash scripts/db-migrate.sh
```

What it does:
- restores `.venv` from `requirements.txt`
- starts PostgreSQL via root `docker-compose.yml`
- applies `0_initial_crawldb.sql` when needed
- applies numbered migrations like `01_*.sql`

Current migration highlights:
- `01_canonical_urls_and_hashes.sql`: canonical URL + dedup support for crawler pages
- `02_manager_control_plane.sql`: creates `manager` schema with daemon/worker/settings/token/command tables used by the manager control plane

To save dependency updates back to `requirements.txt`:

```bash
bash scripts/venv-dump.sh
```

## Add a new migration

1. Add a new SQL file in `db/migrations/` using a two-digit prefix, e.g. `02_add_indexes.sql`.
2. Make the migration idempotent (`IF NOT EXISTS`) so reruns are safe.
3. Apply it:

```bash
bash scripts/db-migrate.sh
```

## Reset database

Use:

```bash
bash scripts/reset-db.sh
```

This drops `crawldb` and reapplies all migrations.
