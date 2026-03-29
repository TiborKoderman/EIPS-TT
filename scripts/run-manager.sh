#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/project-env.sh

ensure_project_env
source_project_env

export ASPNETCORE_ENVIRONMENT="${ASPNETCORE_ENVIRONMENT:-Development}"
export ASPNETCORE_URLS="${ASPNETCORE_URLS:-http://127.0.0.1:5160}"

resolve_primary_manager_url() {
  python3 - <<'PY'
import os

urls = os.environ.get("ASPNETCORE_URLS", "http://127.0.0.1:5160")
for raw in urls.split(";"):
    candidate = raw.strip()
    if candidate:
        print(candidate.rstrip("/"))
        break
PY
}

primary_manager_url="$(resolve_primary_manager_url)"

manager_port_status() {
  python3 - "${primary_manager_url}" <<'PY'
import socket
import sys
from urllib.parse import urlparse

parsed = urlparse(sys.argv[1])
host = parsed.hostname or "127.0.0.1"
if parsed.port is not None:
    port = parsed.port
elif parsed.scheme == "https":
    port = 443
else:
    port = 80

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(1.5)
try:
    sock.connect((host, port))
except OSError:
    print("closed")
else:
    print(f"open:{host}:{port}")
finally:
    sock.close()
PY
}

manager_status="$(manager_port_status)"
if [[ "${manager_status}" == open:* ]]; then
  if curl -fsS "${primary_manager_url}/workers" 2>/dev/null | rg -q "Crawler Manager|Daemon Channel Diagnostics|Manage workers inside crawler daemons"; then
    echo "ManagerApp is already running at ${primary_manager_url}; reusing the existing instance."
    exit 0
  fi

  port_ref="${manager_status#open:}"
  echo "Cannot start ManagerApp: ${primary_manager_url} is already in use by another process (${port_ref})." >&2
  echo "Stop the process using that port or override ASPNETCORE_URLS before rerunning this script." >&2
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Python virtualenv missing; restoring it first..."
  bash scripts/venv-restore.sh
fi

bash scripts/db-migrate.sh
bash scripts/db-info.sh

export ConnectionStrings__CrawldbConnection="Host=${DB_HOST};Port=${DB_PORT};Database=${DB_NAME};Username=${DB_USER};Password=${DB_PASSWORD}"

echo "Starting ManagerApp on ${ASPNETCORE_URLS} using PostgreSQL ${DB_HOST}:${DB_PORT}/${DB_NAME}"
dotnet run --project ManagerApp/ManagerApp.csproj --no-launch-profile
