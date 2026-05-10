#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

source scripts/project-env.sh
ensure_project_env
source_project_env

COMPRESSION="${DUMP_COMPRESSION:-9}"
PA1_DUMP="${PA1_DUMP:-pa1/db}"
PA2_DUMP="${PA2_DUMP:-pa2/extraction-db/crawldb_pa2.dump}"
DUMP_VIA="${DUMP_VIA:-auto}"
PA1_LIMIT_BYTES=$((100 * 1024 * 1024))

usage() {
  cat <<'EOF'
Usage: bash scripts/dump-assignment-dbs.sh [--pa1|--pa2|--all]

Creates separate assignment database dumps in PostgreSQL custom format.

Environment:
  DUMP_COMPRESSION  pg_dump custom-format compression level (default: 9)
  DUMP_VIA          auto, docker, or host (default: auto)
  PA1_DUMP          PA1 output path (default: pa1/db)
  PA2_DUMP          PA2 output path (default: pa2/extraction-db/crawldb_pa2.dump)

PA1 dump:
  - crawldb schema only
  - excludes PA2 vector/link-graph tables
  - excludes operational frontier_queue
  - excludes image and page_data table payloads as required by Assignment 1

PA2 dump:
  - restoreable extraction database payload
  - includes vector extension, crawldb schema, lookup tables, page,
    page_segment_short, page_segment_long, and article_link_graph
  - excludes raw link/frontier/image/page_data tables
EOF
}

want_pa1=false
want_pa2=false

if [[ "$#" -eq 0 ]]; then
  want_pa1=true
  want_pa2=true
fi

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pa1)
      want_pa1=true
      ;;
    --pa2)
      want_pa2=true
      ;;
    --all)
      want_pa1=true
      want_pa2=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

compose_db_running() {
  command -v docker >/dev/null 2>&1 &&
    project_compose ps --services --status running 2>/dev/null | grep -qx 'db'
}

use_docker_dump() {
  case "${DUMP_VIA}" in
    docker)
      return 0
      ;;
    host)
      return 1
      ;;
    auto)
      compose_db_running
      ;;
    *)
      echo "DUMP_VIA must be one of: auto, docker, host" >&2
      exit 2
      ;;
  esac
}

dump_custom() {
  local output="$1"
  shift

  local tmp="${output}.tmp"
  mkdir -p "$(dirname "${output}")"
  rm -f "${tmp}"

  echo "Writing ${output}"
  if use_docker_dump; then
    project_compose exec -T db pg_dump \
      -U "${DB_USER}" \
      -d "${DB_NAME}" \
      -Fc \
      -Z "${COMPRESSION}" \
      "$@" > "${tmp}"
  else
    PGPASSWORD="${DB_PASSWORD}" pg_dump \
      -h "${DB_HOST}" \
      -p "${DB_PORT}" \
      -U "${DB_USER}" \
      -d "${DB_NAME}" \
      -Fc \
      -Z "${COMPRESSION}" \
      "$@" > "${tmp}"
  fi

  pg_restore --list "${tmp}" >/dev/null
  mv "${tmp}" "${output}"

  local bytes
  bytes="$(wc -c < "${output}" | tr -d ' ')"
  printf '  OK: %s (%s bytes)\n' "$(du -h "${output}" | awk '{print $1}')" "${bytes}"
}

if "${want_pa1}"; then
  dump_custom "${PA1_DUMP}" \
    --schema=crawldb \
    --exclude-table=crawldb.page_segment_short \
    --exclude-table=crawldb.page_segment_long \
    --exclude-table=crawldb.article_link_graph \
    --exclude-table=crawldb.frontier_queue \
    --exclude-table-data=crawldb.image \
    --exclude-table-data=crawldb.page_data

  pa1_bytes="$(wc -c < "${PA1_DUMP}" | tr -d ' ')"
  if [[ "${pa1_bytes}" -gt "${PA1_LIMIT_BYTES}" ]]; then
    echo "  WARNING: ${PA1_DUMP} is larger than 100 MiB."
    echo "  Assignment 1 says to use an external link if the custom dump exceeds 100 MB."
  fi
fi

if "${want_pa2}"; then
  # PA2 dump: exclude html_content from page table (bulk crawl data, not needed
  # for retrieval grading) to keep the artifact under ~600 MB. We temporarily
  # null html_content via a backup table, dump, then restore.
  echo "Nulling html_content for PA2 dump (saved to _page_backup_html)..."
  if use_docker_dump; then
    project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -c "
      DROP TABLE IF EXISTS crawldb._page_backup_html;
      CREATE UNLOGGED TABLE crawldb._page_backup_html AS
        SELECT id, html_content FROM crawldb.page WHERE html_content IS NOT NULL;
      UPDATE crawldb.page SET html_content = NULL WHERE html_content IS NOT NULL;
    "
  else
    PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -p "${DB_PORT}" \
      -U "${DB_USER}" -d "${DB_NAME}" -c "
      DROP TABLE IF EXISTS crawldb._page_backup_html;
      CREATE UNLOGGED TABLE crawldb._page_backup_html AS
        SELECT id, html_content FROM crawldb.page WHERE html_content IS NOT NULL;
      UPDATE crawldb.page SET html_content = NULL WHERE html_content IS NOT NULL;
    "
  fi

  dump_custom "${PA2_DUMP}" \
    --extension=vector \
    --schema=crawldb \
    --exclude-table=crawldb.link \
    --exclude-table=crawldb.frontier_queue \
    --exclude-table=crawldb.image \
    --exclude-table=crawldb.page_data \
    --exclude-table=crawldb._page_backup_html

  echo "Restoring html_content from _page_backup_html..."
  if use_docker_dump; then
    project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -c "
      UPDATE crawldb.page p
        SET html_content = b.html_content
        FROM crawldb._page_backup_html b
        WHERE p.id = b.id;
      DROP TABLE crawldb._page_backup_html;
    "
  else
    PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -p "${DB_PORT}" \
      -U "${DB_USER}" -d "${DB_NAME}" -c "
      UPDATE crawldb.page p
        SET html_content = b.html_content
        FROM crawldb._page_backup_html b
        WHERE p.id = b.id;
      DROP TABLE crawldb._page_backup_html;
    "
  fi
  echo "html_content restored."
fi
