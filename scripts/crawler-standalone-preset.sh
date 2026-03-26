#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export CRAWLER_USER_AGENT="fri-wier-EIPS-TT-standalone"
export CRAWLER_WORKERS="1"
export CRAWLER_MIN_DELAY="0.5"
export CRAWLER_DOWNLOAD_TIMEOUT="20"
export CRAWLER_RENDER_TIMEOUT="25"
export CRAWLER_DOWNLOAD_PDF_CONTENT="false"
export CRAWLER_DOWNLOAD_BINARY_CONTENT="true"
export CRAWLER_STORE_LARGE_BINARY_CONTENT="false"
export CRAWLER_FRONTIER_IN_MEMORY_LIMIT="5000"
export CRAWLER_TOPIC_KEYWORDS="medicine,health,clinic,hospital,doctor"

MODE="${1:-frontier}"

case "$MODE" in
  frontier)
    python pa1/crawler/src/main.py \
      --frontier-demo \
      --seed-url "https://nijz.si/" \
      --seed-url "https://www.kclj.si/" \
      --seed-url "https://www.gov.si/"
    ;;
  crawl-once)
    python pa1/crawler/src/main.py \
      --crawl-once-demo \
      --url "http://example.com" \
      --download-binary
    ;;
  api)
    python pa1/crawler/src/main.py \
      --run-api \
      --api-host 127.0.0.1 \
      --api-port 8090
    ;;
  *)
    echo "Unknown mode '$MODE'. Use: frontier | crawl-once | api" >&2
    exit 2
    ;;
esac
