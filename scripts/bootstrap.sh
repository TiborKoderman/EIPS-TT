#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/project-env.sh

bash scripts/venv-restore.sh
mkdir -p pa1/report/.out
bash scripts/db-migrate.sh
source_project_env

echo "Bootstrap complete (.venv + postgres + migration on localhost:${DB_HOST_PORT})."
