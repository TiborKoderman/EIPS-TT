# Migrations and Requirements Workflow

## Apply updates

Use:

```bash
bash scripts/bootstrap.sh
```

What it does:
- rebuilds the app image (`requirements.txt` is installed during Docker build)
- starts/updates the compose stack
- creates `pa1/report/.out`
- applies initial DB migration if schema is missing

## Add a new migration

1. Add a new SQL file in `db/migrations/` (example: `1_add_indexes.sql`).
2. For fresh DB volumes, it is auto-applied on first startup by Postgres init.
3. For an existing DB volume, apply manually:

```bash
docker compose -f .devcontainer/docker-compose.yml exec -T db \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /docker-entrypoint-initdb.d/1_add_indexes.sql
```

## Reset (reverse) DB state

Use:

```bash
bash scripts/reset-db.sh
```

This drops `crawldb` and reapplies the initial migration.
