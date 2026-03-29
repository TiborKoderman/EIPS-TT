#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/project-env.sh

DB_USER="postgres"
DB_NAME="crawldb"
SYSTEM_DB="postgres"

ensure_project_env
source_project_env

print_startup_diagnostics() {
  local recent_logs
  recent_logs="$(project_compose logs --tail=80 db 2>&1 || true)"

  if grep -Fq "Counter to that, there appears to be PostgreSQL data in:" <<<"${recent_logs}"; then
    cat >&2 <<'EOF'
PostgreSQL failed to start because this repo's Docker volume was initialized with an older
image layout and `postgres:latest` now expects the newer Postgres 18+ directory structure.

Run this repo-local reset once to recreate the database volume cleanly:
  bash scripts/reset-db.sh --clean
EOF
    return
  fi

  echo "Recent database logs:" >&2
  echo "${recent_logs}" >&2
}

project_compose up -d db

container_id="$(project_compose ps -q db)"
if [[ -z "${container_id}" ]]; then
  echo "Failed to resolve the database container id." >&2
  exit 1
fi

echo "Waiting for PostgreSQL to become healthy..."
for _ in $(seq 1 60); do
  container_status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || true)"
  if [[ "${container_status}" == "healthy" ]]; then
    break
  fi
  sleep 1
done

if [[ "${container_status}" != "healthy" ]]; then
  echo "PostgreSQL did not become healthy in time."
  print_startup_diagnostics
  exit 1
fi

if ! project_compose exec -T db psql -U "${DB_USER}" -d "${SYSTEM_DB}" -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}';" | grep -q "1"; then
  project_compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${SYSTEM_DB}" -c "CREATE DATABASE ${DB_NAME};"
fi

if ! project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -tAc "SELECT to_regclass('crawldb.page');" | grep -q "crawldb.page"; then
  project_compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" -f /db-migrations/00_initial_crawldb.sql
fi

for migration in db/migrations/[0-9][0-9]_*.sql; do
  [ -e "${migration}" ] || continue
  if [[ "$(basename "${migration}")" == "00_initial_crawldb.sql" ]]; then
    continue
  fi
  project_compose exec -T db psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" -f "/db-migrations/$(basename "${migration}")"
done

echo "Database migrations applied on localhost:${DB_HOST_PORT}."
