# EIPS-TT - Preferential Crawler (PA1)

Quick setup for a reproducible environment with Python, PostgreSQL, and LaTeX.

## Requirements
- Docker Engine + Docker Compose
- Visual Studio Code (recommended)

## Restore workspace after clone

This repository uses Dev Containers so the environment works the same way for all team members.
### VS Code (recommended, easiest)

1. Open the repository in VS Code.
2. Run `Dev Containers: Reopen in Container`.
3. Wait for initialization to finish.

### Non-VS Code setup

Run these Docker Compose commands from repository root (platform-independent):

```bash
docker compose -f .devcontainer/docker-compose.yml up -d --build
docker compose -f .devcontainer/docker-compose.yml exec -T --user vscode app bash -lc "cd /workspaces/<repo-name> && mkdir -p pa1/report/.out"
docker compose -f .devcontainer/docker-compose.yml exec -T db bash -lc "if psql -U postgres -d postgres -tAc \"SELECT to_regclass('crawldb.page');\" | grep -q \"crawldb.page\"; then echo \"Crawldb schema already initialized.\"; else psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /docker-entrypoint-initdb.d/0_initial_crawldb.sql; fi"
```

Or use the provided bootstrap script:

```bash
bash scripts/bootstrap.sh
```

`bootstrap.sh` is the update script too: rerun it after changing `requirements.txt` or migrations.

DB connection string for external DB tools:

`postgresql://postgres:postgres@localhost:5432/postgres`

If port `5432` is already used on your host, adjust the mapping in `.devcontainer/docker-compose.yml`.

Quick check from inside container:

```bash
test -f /.dockerenv && echo "inside container" || echo "on host"
```

No local Python, PostgreSQL, or LaTeX installation is required on the host.
