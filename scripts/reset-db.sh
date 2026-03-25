#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

docker compose up -d db
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d postgres -c "DROP SCHEMA IF EXISTS crawldb CASCADE;"
bash scripts/db-migrate.sh

echo "Database reset complete."
