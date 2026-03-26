#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/project-env.sh

ensure_project_env
source_project_env

export ASPNETCORE_ENVIRONMENT="${ASPNETCORE_ENVIRONMENT:-Development}"
export ASPNETCORE_URLS="${ASPNETCORE_URLS:-http://127.0.0.1:5160}"
export ConnectionStrings__CrawldbConnection="Host=${DB_HOST};Port=${DB_PORT};Database=${DB_NAME};Username=${DB_USER};Password=${DB_PASSWORD}"

echo "Starting ManagerApp on ${ASPNETCORE_URLS} using PostgreSQL ${DB_HOST}:${DB_PORT}/${DB_NAME}"
dotnet run --project ManagerApp/ManagerApp.csproj --no-launch-profile
