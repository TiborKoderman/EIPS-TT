#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/project-env.sh

ensure_project_env
source_project_env

states_csv="${1:-done,failed,completed}"
IFS=',' read -r -a states <<< "$states_csv"

if [[ ${#states[@]} -eq 0 ]]; then
  echo "No states provided. Example: scripts/frontier-cleanup.sh done,failed"
  exit 1
fi

quoted_states=()
for state in "${states[@]}"; do
  trimmed="$(echo "$state" | xargs)"
  if [[ -n "$trimmed" ]]; then
    quoted_states+=("'${trimmed}'")
  fi
done

if [[ ${#quoted_states[@]} -eq 0 ]]; then
  echo "No valid states after parsing input: $states_csv"
  exit 1
fi

state_list="$(IFS=,; echo "${quoted_states[*]}")"

echo "Cleaning crawldb.frontier_queue terminal states: ${states_csv}"

docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" <<SQL
BEGIN;
SELECT state, count(*) AS rows_before
FROM crawldb.frontier_queue
GROUP BY state
ORDER BY state;

DELETE FROM crawldb.frontier_queue
WHERE lower(state) IN (${state_list});

SELECT state, count(*) AS rows_after
FROM crawldb.frontier_queue
GROUP BY state
ORDER BY state;
COMMIT;
SQL

echo "Frontier cleanup complete."
