#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

DB_USER="postgres"
DB_NAME="crawldb"
SYSTEM_DB="postgres"

docker compose up -d db

if ! docker compose exec -T db psql -U "${DB_USER}" -d "${SYSTEM_DB}" -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}';" | grep -q "1"; then
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${SYSTEM_DB}" -c "CREATE DATABASE ${DB_NAME};"
fi

docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" -c "DROP SCHEMA IF EXISTS crawldb CASCADE;"
bash scripts/db-migrate.sh

echo "Database reset complete."
