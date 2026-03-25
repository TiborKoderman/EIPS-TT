#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

bash scripts/venv-restore.sh
mkdir -p pa1/report/.out
bash scripts/db-migrate.sh

echo "Bootstrap complete (.venv + postgres + migration)."
