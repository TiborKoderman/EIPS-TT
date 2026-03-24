#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE_FILE=".devcontainer/docker-compose.yml"
REPO_NAME="$(basename "$PWD")"

docker compose -f "$COMPOSE_FILE" up -d --build

docker compose -f "$COMPOSE_FILE" exec -T --user vscode app bash -lc \
  "cd /workspaces/$REPO_NAME && mkdir -p pa1/report/.out"

docker compose -f "$COMPOSE_FILE" exec -T db bash -lc \
  "if psql -U postgres -d postgres -tAc \"SELECT to_regclass('crawldb.page');\" | grep -q \"crawldb.page\"; then echo \"Crawldb schema already initialized.\"; else psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /docker-entrypoint-initdb.d/0_initial_crawldb.sql; fi"

echo "Bootstrap complete."
