#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-postgres}"
DB_NAME="${DB_NAME:-postgres}"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p pa1/report/.out

for i in $(seq 1 60); do
  if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1; then
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "PostgreSQL is not reachable at $DB_HOST:$DB_PORT after 60 seconds."
    exit 1
  fi
  sleep 1
done

export PGPASSWORD="$DB_PASSWORD"

if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT to_regclass('crawldb.page');" | grep -q "crawldb.page"; then
  echo "Crawldb schema already initialized."
else
  psql -v ON_ERROR_STOP=1 \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -f pa1/crawler/db/sql/migrations/0_initial_crawldb.sql
  echo "Applied initial crawldb migration."
fi

echo "Post-create setup complete."
