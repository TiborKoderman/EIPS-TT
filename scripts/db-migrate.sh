#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

DB_USER="postgres"
DB_NAME="crawldb"
SYSTEM_DB="postgres"

docker compose up -d db

echo "Waiting for PostgreSQL to become ready..."
for _ in $(seq 1 30); do
  if docker compose exec -T db pg_isready -U "${DB_USER}" -d "${SYSTEM_DB}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker compose exec -T db pg_isready -U "${DB_USER}" -d "${SYSTEM_DB}" >/dev/null 2>&1; then
  echo "PostgreSQL did not become ready in time."
  exit 1
fi

if ! docker compose exec -T db psql -U "${DB_USER}" -d "${SYSTEM_DB}" -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}';" | grep -q "1"; then
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${SYSTEM_DB}" -c "CREATE DATABASE ${DB_NAME};"
fi

if ! docker compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -tAc "SELECT to_regclass('crawldb.page');" | grep -q "crawldb.page"; then
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" -f /docker-entrypoint-initdb.d/0_initial_crawldb.sql
fi

for migration in db/migrations/[0-9][0-9]_*.sql; do
  [ -e "${migration}" ] || continue
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" -f "/docker-entrypoint-initdb.d/$(basename "${migration}")"
done

echo "Database migrations applied."
