#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/project-env.sh

ensure_project_env
source_project_env

MANAGER_BASE_URL="${MANAGER_BASE_URL:-http://127.0.0.1:5175}"
FRONTIER_TOKEN="${FRONTIER_TOKEN:-${CRAWLER_API_TOKEN:-}}"

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required for integration smoke tests." >&2
  exit 1
fi

http_get() {
  local url="$1"
  if [[ -n "${FRONTIER_TOKEN}" ]]; then
    curl -fsS -H "Authorization: Bearer ${FRONTIER_TOKEN}" "${url}"
  else
    curl -fsS "${url}"
  fi
}

http_post_json() {
  local url="$1"
  local payload="$2"
  if [[ -n "${FRONTIER_TOKEN}" ]]; then
    curl -fsS -H "Content-Type: application/json" -H "Authorization: Bearer ${FRONTIER_TOKEN}" -X POST "${url}" -d "${payload}"
  else
    curl -fsS -H "Content-Type: application/json" -X POST "${url}" -d "${payload}"
  fi
}

db_scalar() {
  local sql="$1"
  project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -t -A -c "${sql}" | tr -d '[:space:]'
}

assert_eq() {
  local actual="$1"
  local expected="$2"
  local message="$3"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "ASSERT FAILED: ${message} (expected=${expected}, actual=${actual})" >&2
    exit 1
  fi
}

assert_nonempty() {
  local value="$1"
  local message="$2"
  if [[ -z "${value}" ]]; then
    echo "ASSERT FAILED: ${message}" >&2
    exit 1
  fi
}

host_of_url() {
  echo "$1" | sed -E 's#https?://([^/]+)/?.*#\1#'
}

echo "Checking manager API at ${MANAGER_BASE_URL}"
http_get "${MANAGER_BASE_URL}/api/crawler/events?limit=1" >/dev/null

TEST_RUN_ID="$(date +%s)-$RANDOM"
BASE_HOST="itest-${TEST_RUN_ID}.example.test"
OTHER_HOST="other-${TEST_RUN_ID}.example.test"
INGEST_HOST="ingest-${TEST_RUN_ID}.example.test"

cleanup_sql=$(cat <<SQL
WITH doomed AS (
  SELECT id
  FROM crawldb.page
  WHERE url LIKE 'https://${BASE_HOST}/%'
     OR url LIKE 'https://${OTHER_HOST}/%'
     OR url LIKE 'https://${INGEST_HOST}/%'
)
DELETE FROM crawldb.link
WHERE from_page IN (SELECT id FROM doomed)
   OR to_page IN (SELECT id FROM doomed);

DELETE FROM crawldb.page
WHERE url LIKE 'https://${BASE_HOST}/%'
   OR url LIKE 'https://${OTHER_HOST}/%'
   OR url LIKE 'https://${INGEST_HOST}/%';

DELETE FROM crawldb.frontier_queue
WHERE url LIKE 'https://${BASE_HOST}/%'
   OR url LIKE 'https://${OTHER_HOST}/%'
   OR url LIKE 'https://${INGEST_HOST}/%';
SQL
)

project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -c "${cleanup_sql}" >/dev/null

echo "[1/4] Collision dedupe on /api/frontier/seed"
collision_url="https://${BASE_HOST}/collision"
for _ in $(seq 1 12); do
  http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${collision_url}\",\"priority\":990000,\"depth\":0}" >/dev/null &
done
wait

collision_rows="$(db_scalar "SELECT COUNT(*) FROM crawldb.frontier_queue WHERE url = '${collision_url}';")"
assert_eq "${collision_rows}" "1" "frontier seed collision should keep one queue row"

echo "[2/4] Dequeue/complete/requeue flow with politeness skip"
url_same_1="https://${BASE_HOST}/same/a"
url_same_2="https://${BASE_HOST}/same/b"
url_other="https://${OTHER_HOST}/other/a"

http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${url_same_1}\",\"priority\":980000,\"depth\":0}" >/dev/null
http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${url_same_2}\",\"priority\":970000,\"depth\":0}" >/dev/null
http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${url_other}\",\"priority\":960000,\"depth\":0}" >/dev/null

dequeue_payload='{"workerIds":[9101,9102],"limit":2,"daemonId":"itest-daemon"}'
dequeue_json="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/dequeue" "${dequeue_payload}")"

item_count="$(echo "${dequeue_json}" | jq -r '.data.items | length')"
assert_eq "${item_count}" "2" "dequeue should return two claims"

url1="$(echo "${dequeue_json}" | jq -r '.data.items[0].url')"
url2="$(echo "${dequeue_json}" | jq -r '.data.items[1].url')"
lease1="$(echo "${dequeue_json}" | jq -r '.data.items[0].leaseToken')"
lease2="$(echo "${dequeue_json}" | jq -r '.data.items[1].leaseToken')"
worker1="$(echo "${dequeue_json}" | jq -r '.data.items[0].workerId')"
worker2="$(echo "${dequeue_json}" | jq -r '.data.items[1].workerId')"

assert_nonempty "${url1}" "first dequeue item URL missing"
assert_nonempty "${url2}" "second dequeue item URL missing"
assert_nonempty "${lease1}" "first dequeue lease token missing"
assert_nonempty "${lease2}" "second dequeue lease token missing"

host1="$(host_of_url "${url1}")"
host2="$(host_of_url "${url2}")"
if [[ "${host1}" == "${host2}" ]]; then
  echo "ASSERT FAILED: expected politeness-aware dequeue to avoid same host back-to-back when alternative exists" >&2
  echo "claims: ${url1} | ${url2}" >&2
  exit 1
fi

priority1="$(db_scalar "SELECT priority FROM crawldb.frontier_queue WHERE url = '${url1}' LIMIT 1;")"
priority2="$(db_scalar "SELECT priority FROM crawldb.frontier_queue WHERE url = '${url2}' LIMIT 1;")"

requeue_url="${url1}"
requeue_lease="${lease1}"
requeue_worker="${worker1}"
complete_worker="${worker2}"
complete_url="${url2}"
complete_lease="${lease2}"

if [[ "${priority2}" -gt "${priority1}" ]]; then
  requeue_worker="${worker2}"
  requeue_url="${url2}"
  requeue_lease="${lease2}"
  complete_worker="${worker1}"
  complete_url="${url1}"
  complete_lease="${lease1}"
fi

complete_resp="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/complete" "{\"workerId\":${complete_worker},\"url\":\"${complete_url}\",\"leaseToken\":\"${complete_lease}\",\"status\":\"completed\",\"daemonId\":\"itest-daemon\"}")"
complete_ok="$(echo "${complete_resp}" | jq -r '.data.completed // false')"
assert_eq "${complete_ok}" "true" "completed-state transition should succeed"

requeue_resp="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/complete" "{\"workerId\":${requeue_worker},\"url\":\"${requeue_url}\",\"leaseToken\":\"${requeue_lease}\",\"status\":\"queued\",\"daemonId\":\"itest-daemon\"}")"
requeue_ok="$(echo "${requeue_resp}" | jq -r '.data.completed // false')"
assert_eq "${requeue_ok}" "true" "queued-state requeue transition should succeed"

requeued_state="$(db_scalar "SELECT state::text FROM crawldb.frontier_queue WHERE url = '${requeue_url}' LIMIT 1;")"
assert_eq "${requeued_state}" "QUEUED" "requeue completion should place the URL back into QUEUED state"

# Politeness cooldown may skip same-host URLs briefly after completion.
sleep 1

echo "[3/4] Lease expiry requeue"
lease_url="https://${BASE_HOST}/lease-expiry"
http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${lease_url}\",\"priority\":995000,\"depth\":0}" >/dev/null

claim_one="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/claim" '{"workerId":9201,"daemonId":"itest-daemon"}')"
claim_one_url="$(echo "${claim_one}" | jq -r '.data.url // empty')"
claim_one_token="$(echo "${claim_one}" | jq -r '.data.leaseToken // empty')"
assert_eq "${claim_one_url}" "${lease_url}" "lease test URL must be claimed first"
assert_nonempty "${claim_one_token}" "lease token missing from first claim"

status_json="$(http_get "${MANAGER_BASE_URL}/api/frontier/status")"
lease_ttl="$(echo "${status_json}" | jq -r '.data.leaseTtlSeconds // 30')"
echo "Forcing lease timestamp older than ttl=${lease_ttl}s"
project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -c "UPDATE crawldb.frontier_queue SET locked_at = NOW() - make_interval(secs => ${lease_ttl} + 5) WHERE url = '${lease_url}';" >/dev/null

http_get "${MANAGER_BASE_URL}/api/frontier/status" >/dev/null
lease_state="$(db_scalar "SELECT state::text FROM crawldb.frontier_queue WHERE url = '${lease_url}' LIMIT 1;")"

stale_complete_resp="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/complete" "{\"workerId\":9201,\"url\":\"${lease_url}\",\"leaseToken\":\"${claim_one_token}\",\"status\":\"completed\",\"daemonId\":\"itest-daemon\"}")"
stale_complete_ok="$(echo "${stale_complete_resp}" | jq -r '.data.completed // false')"
assert_eq "${stale_complete_ok}" "false" "expired lease token must not be accepted for completion"

if [[ "${lease_state}" == "QUEUED" ]]; then
  :
elif [[ "${lease_state}" == "LOCKED" ]]; then
  replacement_token="$(db_scalar "SELECT COALESCE(lease_token, '') FROM crawldb.frontier_queue WHERE url = '${lease_url}' LIMIT 1;")"
  assert_nonempty "${replacement_token}" "relocked lease should have a replacement lease token"
  if [[ "${replacement_token}" == "${claim_one_token}" ]]; then
    echo "ASSERT FAILED: lease token must rotate after expiry when URL is re-locked" >&2
    exit 1
  fi
else
  echo "ASSERT FAILED: expired lease should transition to QUEUED or LOCKED (actual=${lease_state})" >&2
  exit 1
fi

echo "[4/4] Concurrent ingest dedupe race"
ingest_root="https://${INGEST_HOST}/root"
ingest_d1="https://${INGEST_HOST}/doc/a"
ingest_d2="https://${INGEST_HOST}/doc/b"
ingest_payload="{\"rawUrl\":\"${ingest_root}\",\"discoveredUrls\":[\"${ingest_d1}\",\"${ingest_d2}\",\"${ingest_d1}\"],\"downloadResult\":{\"finalUrl\":\"${ingest_root}\",\"statusCode\":200,\"pageTypeCode\":\"HTML\",\"htmlContent\":\"<html><body>itest-${TEST_RUN_ID}</body></html>\"}}"

ingest_failures=0
for _ in $(seq 1 10); do
  if ! http_post_json "${MANAGER_BASE_URL}/api/crawler/ingest" "${ingest_payload}" >/dev/null; then
    ingest_failures=$((ingest_failures + 1))
  fi
done
assert_eq "${ingest_failures}" "0" "concurrent ingest requests should not fail"

ingest_page_rows="$(db_scalar "SELECT COUNT(*) FROM crawldb.page WHERE url = '${ingest_root}';")"
assert_eq "${ingest_page_rows}" "1" "ingest root URL should be unique in crawldb.page"

ingest_frontier_rows="$(db_scalar "SELECT COUNT(*) FROM crawldb.frontier_queue WHERE url IN ('${ingest_d1}','${ingest_d2}');")"
assert_eq "${ingest_frontier_rows}" "2" "discovered URLs should be deduped into unique frontier rows"

echo "Integration smoke passed (run id: ${TEST_RUN_ID})"
