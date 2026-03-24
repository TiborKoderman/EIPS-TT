#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p pa1/report/.out

echo "Post-create setup complete."
