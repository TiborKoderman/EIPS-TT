# EIPS-TT - Preferential Crawler (PA1)

Single-compose setup with two modes:
- host mode: local `.venv` + `db` service
- devcontainer mode: `app` + `db` services

`docker-compose.yml` is shared by both modes. If you only want host mode, do not start `app`.

## Recommended Workflow

```bash
# restore python env from requirements.txt
bash scripts/venv-restore.sh

# start postgres and apply migrations
bash scripts/db-migrate.sh
```

When dependencies change:

```bash
# save current .venv packages back to requirements.txt
bash scripts/venv-dump.sh
```

## Host Mode (No Devcontainer)

```bash
bash scripts/venv-restore.sh
source .venv/bin/activate
docker compose up -d db
bash scripts/db-migrate.sh
python pa1/crawler/src/db/pg_connect.py
```

## Devcontainer Mode (Optional)

1. Open this repository folder in VS Code.
2. Run `Dev Containers: Rebuild and Reopen in Container`.
3. The container uses `/usr/local/bin/python`, while host mode uses `.venv/bin/python`.

Both interpreter paths are only active in their own context.

Non-VS Code equivalent (for quick container checks):

```bash
docker compose --profile devcontainer up -d app
```

## Database Connection

Use this from DB tools:

`postgresql://postgres:postgres@localhost:5432/crawldb`

Credentials are local to your compose stack.

## Utility Commands

```bash
# full setup shortcut (.venv + db + migrations)
bash scripts/bootstrap.sh

# reset schema, then re-apply migrations
bash scripts/reset-db.sh

# db logs
docker compose logs -f db
```

## Optional Devcontainer Notes

See `.devcontainer/NOTE.md`.
