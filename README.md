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

Run one command from repository root:

```bash
bash scripts/bootstrap.sh
```

This runs:
- `docker compose up -d --build`
- Python/pip setup inside the `app` container
- DB migration inside the `db` container (if schema is not initialized)

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
