#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE_FILE=".devcontainer/docker-compose.yml"

docker compose -f "$COMPOSE_FILE" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d postgres -c "DROP SCHEMA IF EXISTS crawldb CASCADE;"
docker compose -f "$COMPOSE_FILE" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /docker-entrypoint-initdb.d/0_initial_crawldb.sql

echo "Database reset complete."
