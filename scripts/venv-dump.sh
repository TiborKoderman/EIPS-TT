#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -x .venv/bin/python ]; then
  echo ".venv is missing. Run bash scripts/venv-restore.sh first."
  exit 1
fi

source .venv/bin/activate
python -m pip freeze | grep -Ev '^pkg(-|_)resources==' | sort -f > requirements.txt

echo "requirements.txt updated from .venv"
