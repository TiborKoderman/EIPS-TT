#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE=".devcontainer/docker-compose.yml"
REPO_NAME="$(basename "$ROOT_DIR")"

bash .devcontainer/pre-create.sh

docker compose -f "$COMPOSE_FILE" up -d --build

docker compose -f "$COMPOSE_FILE" exec -T --user vscode app bash -lc "cd /workspaces/$REPO_NAME && bash .devcontainer/post-create.sh"

echo "Bootstrap complete."
