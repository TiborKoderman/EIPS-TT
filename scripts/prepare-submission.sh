#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "--inside" ]]; then
  shift
fi

if ! command -v latexmk >/dev/null 2>&1; then
  echo "latexmk is required to build pa1/report/report.pdf."
  echo "Install a TeX distribution that provides latexmk (for example texlive-full)."
  exit 1
fi

cd pa1/report
if ! latexmk -pdf -interaction=nonstopmode -file-line-error -outdir=.out report.tex; then
  echo "latexmk reported errors; continuing to verify whether pa1/report/.out/report.pdf was produced."
fi
cd "$ROOT_DIR"

if [ ! -f pa1/report/.out/report.pdf ]; then
  echo "Missing pa1/report/.out/report.pdf after build."
  exit 1
fi

cp README.md pa1/README.md
cp pa1/report/.out/report.pdf pa1/report.pdf

echo "Prepared submission artifacts: pa1/README.md and pa1/report.pdf"
