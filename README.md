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

- Python dependencies from `requirements.txt`
- PostgreSQL readiness check
- Initial DB migration (`0_initial_crawldb.sql`) if crawldb is not initialized yet
- Report output directory creation (`pa1/report/.out`)

### Non-VS Code setup

Run one command from repository root:

```bash
bash scripts/bootstrap.sh
```

This runs the same pre-create and post-create automation used by Dev Containers.

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
