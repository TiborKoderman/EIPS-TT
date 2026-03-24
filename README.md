# EIPS-TT - Preferential Crawler (PA1)

Quick setup for a reproducible environment with Python, PostgreSQL, and LaTeX.

## Requirements
- Docker Engine + Docker Compose
- Visual Studio Code (recommended)

## Setup

This repository uses Dev Containers so the environment works the same way for all team members.
### VS Code (recommended)

1. Open the repository in VS Code.
2. Run `Dev Containers: Reopen in Container`.
3. Wait for post-create initialization to finish.

What is automated during container initialization:

- Docker Compose stack startup (`app` + `db`)
- Python dependencies from `requirements.txt`
- `pip` upgrade
- Initial DB migration from `db/migrations` on first PostgreSQL volume initialization
- Report output directory creation (`pa1/report/.out`)

### Non-VS Code setup

Run these Docker Compose commands from repository root:

```bash
docker compose -f .devcontainer/docker-compose.yml up -d --build
docker compose -f .devcontainer/docker-compose.yml exec -T --user vscode app bash -lc "cd /workspaces/EIPS-TT && python -m pip install --upgrade pip && python -m pip install -r requirements.txt && mkdir -p pa1/report/.out"
docker compose -f .devcontainer/docker-compose.yml exec -T db bash -lc "if psql -U postgres -d postgres -tAc \"SELECT to_regclass('crawldb.page');\" | grep -q \"crawldb.page\"; then echo \"Crawldb schema already initialized.\"; else psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /docker-entrypoint-initdb.d/0_initial_crawldb.sql; fi"
```

Or use the provided bootstrap script:

```bash
bash scripts/bootstrap.sh
```

## Environment & Credentials

- PostgreSQL runs in the local Docker stack (container `db`), not on your host machine.
- Current credentials are defined in `.devcontainer/docker-compose.yml`:
  - user: `postgres`
  - password: `postgres`
  - database: `postgres`
- Inside the `app` container, `localhost:5432` points to the shared DB network namespace.

## Useful checks

- Confirm container status:

```bash
docker compose -f .devcontainer/docker-compose.yml ps
```

- Confirm you are inside a container shell:

```bash
test -f /.dockerenv && echo "inside container" || echo "on host"
```

## Maintenance commands

- Prepare submission files (`pa1/report.pdf`, `pa1/README.md`):

```bash
bash scripts/prepare-submission.sh
```

- Reset database schema and reapply migration:

```bash
bash scripts/reset-db.sh
```

No local Python, PostgreSQL, or LaTeX installation is required on the host.
