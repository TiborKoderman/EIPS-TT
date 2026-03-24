#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" != "--inside" ]] && [[ ! -f /.dockerenv ]]; then
  COMPOSE_FILE=".devcontainer/docker-compose.yml"
  REPO_NAME="$(basename "$ROOT_DIR")"
  docker compose -f "$COMPOSE_FILE" exec -T --user vscode app bash -lc "cd /workspaces/$REPO_NAME && bash scripts/prepare-submission.sh --inside"
  exit 0
fi

cd pa1/report
latexmk -pdf -interaction=nonstopmode -file-line-error -outdir=.out report.tex
cd "$ROOT_DIR"

if [ ! -f pa1/report/.out/report.pdf ]; then
  echo "Missing pa1/report/.out/report.pdf after build."
  exit 1
fi

cp README.md pa1/README.md
cp pa1/report/.out/report.pdf pa1/report.pdf

echo "Prepared submission artifacts: pa1/README.md and pa1/report.pdf"
