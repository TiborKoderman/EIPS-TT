#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but not found."
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p pa1/report/.out

docker compose up -d db

echo "Waiting for PostgreSQL to become ready..."
for _ in $(seq 1 30); do
  if docker compose exec -T db pg_isready -U postgres -d postgres >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker compose exec -T db pg_isready -U postgres -d postgres >/dev/null 2>&1; then
  echo "PostgreSQL did not become ready in time."
  exit 1
fi

docker compose exec -T db bash -lc \
  "if psql -U postgres -d postgres -tAc \"SELECT to_regclass('crawldb.page');\" | grep -q \"crawldb.page\"; then echo \"Crawldb schema already initialized.\"; else psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /docker-entrypoint-initdb.d/0_initial_crawldb.sql; fi"

echo "Bootstrap complete (.venv + postgres + migration)."
