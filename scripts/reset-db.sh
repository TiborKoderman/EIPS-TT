#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" != "--inside" ]] && [[ ! -f /.dockerenv ]]; then
	COMPOSE_FILE=".devcontainer/docker-compose.yml"
	REPO_NAME="$(basename "$ROOT_DIR")"
	docker compose -f "$COMPOSE_FILE" exec -T --user vscode app bash -lc "cd /workspaces/$REPO_NAME && bash scripts/reset-db.sh --inside"
	exit 0
fi

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-postgres}"
DB_NAME="${DB_NAME:-postgres}"

export PGPASSWORD="$DB_PASSWORD"

psql -v ON_ERROR_STOP=1 -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "DROP SCHEMA IF EXISTS crawldb CASCADE;"
psql -v ON_ERROR_STOP=1 -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f pa1/crawler/db/sql/migrations/0_initial_crawldb.sql

echo "Database reset complete."
