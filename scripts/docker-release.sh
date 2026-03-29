#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

OWNER="${GHCR_OWNER:-${GITHUB_REPOSITORY_OWNER:-}}"
if [[ -z "${OWNER}" ]]; then
  echo "Set GHCR_OWNER or GITHUB_REPOSITORY_OWNER before running." >&2
  exit 2
fi

TAG="${1:-latest}"
CRAWLER_IMAGE="ghcr.io/${OWNER}/eips-tt-crawler:${TAG}"
SERVER_CRAWLER_IMAGE="ghcr.io/${OWNER}/eips-tt-server-crawler:${TAG}"

echo "Building ${CRAWLER_IMAGE}"
docker build -f pa1/crawler/Dockerfile -t "${CRAWLER_IMAGE}" .

echo "Building ${SERVER_CRAWLER_IMAGE}"
docker build -f ManagerApp/Dockerfile.server-crawler -t "${SERVER_CRAWLER_IMAGE}" .

if [[ "${PUSH:-0}" == "1" ]]; then
  echo "Pushing ${CRAWLER_IMAGE}"
  docker push "${CRAWLER_IMAGE}"
  echo "Pushing ${SERVER_CRAWLER_IMAGE}"
  docker push "${SERVER_CRAWLER_IMAGE}"
else
  echo "Build complete. Set PUSH=1 to publish to GHCR."
fi
