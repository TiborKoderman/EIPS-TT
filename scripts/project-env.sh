#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ENV_FILE="${ROOT_DIR}/.env.local"

port_is_available() {
  local port="$1"
  python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

pick_db_host_port() {
  if [[ -n "${DB_HOST_PORT:-}" ]]; then
    printf '%s\n' "${DB_HOST_PORT}"
    return
  fi

  local port
  for port in 5432 5433 5434 5435 5436 5437 5438 5439 5440; do
    if port_is_available "${port}"; then
      printf '%s\n' "${port}"
      return
    fi
  done

  echo "Unable to find an available PostgreSQL host port in the 5432-5440 range." >&2
  exit 1
}

write_project_env() {
  local host_port="$1"
  cat > "${PROJECT_ENV_FILE}" <<EOF
DB_HOST_PORT=${host_port}
DB_HOST=localhost
DB_PORT=${host_port}
DB_USER=postgres
DB_PASSWORD=postgres
DB_NAME=crawldb
EOF
}

ensure_project_env() {
  if [[ -f "${PROJECT_ENV_FILE}" ]]; then
    return
  fi

  local host_port
  host_port="$(pick_db_host_port)"
  write_project_env "${host_port}"
}

source_project_env() {
  if [[ -f "${PROJECT_ENV_FILE}" ]]; then
    set -a
    source "${PROJECT_ENV_FILE}"
    set +a
  fi
}

project_compose() {
  docker compose --env-file "${PROJECT_ENV_FILE}" "$@"
}
