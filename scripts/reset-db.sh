#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

DB_USER="postgres"
DB_NAME="crawldb"
SYSTEM_DB="postgres"
CLEAN_RESET=0

usage() {
  cat <<'EOF'
Usage: bash scripts/reset-db.sh [--clean]

Options:
  --clean   Full reset: removes compose containers/networks/volumes before recreating DB.
EOF
}

while (($# > 0)); do
  case "$1" in
    --clean)
      CLEAN_RESET=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if (( CLEAN_RESET )); then
  echo "Performing full clean reset (containers + volumes)..."
  docker compose down -v --remove-orphans
  bash scripts/db-migrate.sh
  echo "Database clean reset complete."
  exit 0
fi

docker compose up -d db

if ! docker compose exec -T db psql -U "${DB_USER}" -d "${SYSTEM_DB}" -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}';" | grep -q "1"; then
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${SYSTEM_DB}" -c "CREATE DATABASE ${DB_NAME};"
fi

docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" -c "DROP SCHEMA IF EXISTS crawldb CASCADE;"
bash scripts/db-migrate.sh

echo "Database reset complete."
