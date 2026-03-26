#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/project-env.sh

ensure_project_env
source_project_env

cat <<EOF
PostgreSQL connection details
  Host: ${DB_HOST}
  Port: ${DB_PORT}
  Database: ${DB_NAME}
  Username: ${DB_USER}
  Password: ${DB_PASSWORD}
  URL: postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}

DBeaver fields
  Host: ${DB_HOST}
  Port: ${DB_PORT}
  Database: ${DB_NAME}
  Username: ${DB_USER}
  Password: ${DB_PASSWORD}
EOF

python3 - <<'PY'
import os
import socket
import sys

host = os.environ["DB_HOST"]
port = int(os.environ["DB_PORT"])

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(3)
try:
    sock.connect((host, port))
except OSError as exc:
    print(f"TCP check: FAILED to connect to {host}:{port} ({exc})")
    sys.exit(1)
finally:
    sock.close()

print(f"TCP check: OK ({host}:{port})")
PY

if [[ -x ".venv/bin/python" ]]; then
  .venv/bin/python - <<'PY'
import os
import sys

try:
    import psycopg2
except ImportError:
    print("SQL check: skipped (.venv exists but psycopg2 is not installed)")
    sys.exit(0)

conn = psycopg2.connect(
    host=os.environ["DB_HOST"],
    port=os.environ["DB_PORT"],
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)
cur = conn.cursor()
cur.execute(
    "select current_database(), current_user, inet_server_addr()::text, inet_server_port()"
)
database, username, server_addr, server_port = cur.fetchone()
cur.close()
conn.close()

print(
    "SQL check: OK "
    f"(db={database}, user={username}, server={server_addr}:{server_port})"
)
PY
else
  echo "SQL check: skipped (.venv/bin/python not found yet)"
fi
