#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/project-env.sh

ensure_project_env
source_project_env

MANAGER_BASE_URL="${MANAGER_BASE_URL:-http://127.0.0.1:5175}"
FRONTIER_TOKEN="${FRONTIER_TOKEN:-${CRAWLER_API_TOKEN:-}}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required for integration smoke tests." >&2
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

json_path_get() {
  local json_input="$1"
  local path="$2"
  local default_value="$3"
  JSON_INPUT="${json_input}" python3 - "$path" "$default_value" <<'PY'
import json
import os
import sys

path = sys.argv[1]
default = sys.argv[2]
raw = os.environ.get("JSON_INPUT", "")

try:
  value = json.loads(raw)
except Exception:
  print(default)
  raise SystemExit(0)

for segment in path.split('.'):
  if segment == '':
    continue
  if isinstance(value, list):
    if not segment.isdigit():
      print(default)
      raise SystemExit(0)
    index = int(segment)
    if index < 0 or index >= len(value):
      print(default)
      raise SystemExit(0)
    value = value[index]
  elif isinstance(value, dict):
    if segment not in value:
      print(default)
      raise SystemExit(0)
    value = value[segment]
  else:
    print(default)
    raise SystemExit(0)

if value is None:
  print(default)
elif isinstance(value, bool):
  print("true" if value else "false")
elif isinstance(value, (int, float, str)):
  print(value)
else:
  print(default)
PY
}

json_path_length() {
  local json_input="$1"
  local path="$2"
  JSON_INPUT="${json_input}" python3 - "$path" <<'PY'
import json
import os
import sys

path = sys.argv[1]
raw = os.environ.get("JSON_INPUT", "")

try:
  value = json.loads(raw)
except Exception:
  print(0)
  raise SystemExit(0)

for segment in path.split('.'):
  if segment == '':
    continue
  if isinstance(value, list):
    if not segment.isdigit():
      print(0)
      raise SystemExit(0)
    index = int(segment)
    if index < 0 or index >= len(value):
      print(0)
      raise SystemExit(0)
    value = value[index]
  elif isinstance(value, dict):
    if segment not in value:
      print(0)
      raise SystemExit(0)
    value = value[segment]
  else:
    print(0)
    raise SystemExit(0)

if isinstance(value, list):
  print(len(value))
else:
  print(0)
PY
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

echo "[1/6] Collision dedupe on /api/frontier/seed"
collision_url="https://${BASE_HOST}/collision"
for _ in $(seq 1 12); do
  http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${collision_url}\",\"priority\":990000,\"depth\":0}" >/dev/null &
done
wait

collision_rows="$(db_scalar "SELECT COUNT(*) FROM crawldb.frontier_queue WHERE url = '${collision_url}';")"
assert_eq "${collision_rows}" "1" "frontier seed collision should keep one queue row"

echo "[2/6] Dequeue/complete/requeue flow with politeness skip"
url_same_1="https://${BASE_HOST}/same/a"
url_same_2="https://${BASE_HOST}/same/b"
url_other="https://${OTHER_HOST}/other/a"

http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${url_same_1}\",\"priority\":980000,\"depth\":0}" >/dev/null
http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${url_same_2}\",\"priority\":970000,\"depth\":0}" >/dev/null
http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${url_other}\",\"priority\":960000,\"depth\":0}" >/dev/null

dequeue_payload='{"workerIds":[9101,9102],"limit":2,"daemonId":"itest-daemon"}'
dequeue_json="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/dequeue" "${dequeue_payload}")"

item_count="$(json_path_length "${dequeue_json}" "data.items")"
assert_eq "${item_count}" "2" "dequeue should return two claims"

url1="$(json_path_get "${dequeue_json}" "data.items.0.url" "")"
url2="$(json_path_get "${dequeue_json}" "data.items.1.url" "")"
lease1="$(json_path_get "${dequeue_json}" "data.items.0.leaseToken" "")"
lease2="$(json_path_get "${dequeue_json}" "data.items.1.leaseToken" "")"
worker1="$(json_path_get "${dequeue_json}" "data.items.0.workerId" "0")"
worker2="$(json_path_get "${dequeue_json}" "data.items.1.workerId" "0")"

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
complete_ok="$(json_path_get "${complete_resp}" "data.completed" "false")"
assert_eq "${complete_ok}" "true" "completed-state transition should succeed"

requeue_resp="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/complete" "{\"workerId\":${requeue_worker},\"url\":\"${requeue_url}\",\"leaseToken\":\"${requeue_lease}\",\"status\":\"queued\",\"daemonId\":\"itest-daemon\"}")"
requeue_ok="$(json_path_get "${requeue_resp}" "data.completed" "false")"
assert_eq "${requeue_ok}" "true" "queued-state requeue transition should succeed"

requeued_state="$(db_scalar "SELECT state::text FROM crawldb.frontier_queue WHERE url = '${requeue_url}' LIMIT 1;")"
assert_eq "${requeued_state}" "QUEUED" "requeue completion should place the URL back into QUEUED state"

# Politeness cooldown may skip same-host URLs briefly after completion.
sleep 1

echo "[3/6] Lease expiry requeue"
lease_url="https://${BASE_HOST}/lease-expiry"
http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${lease_url}\",\"priority\":2000000000,\"depth\":0}" >/dev/null

claim_one="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/claim" '{"workerId":9201,"daemonId":"itest-lease"}')"
claim_one_url="$(json_path_get "${claim_one}" "data.url" "")"
claim_one_token="$(json_path_get "${claim_one}" "data.leaseToken" "")"
assert_eq "${claim_one_url}" "${lease_url}" "lease test URL must be claimed first"
assert_nonempty "${claim_one_token}" "lease token missing from first claim"

status_json="$(http_get "${MANAGER_BASE_URL}/api/frontier/status")"
lease_ttl="$(json_path_get "${status_json}" "data.leaseTtlSeconds" "30")"
echo "Forcing lease timestamp older than ttl=${lease_ttl}s"
project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -c "UPDATE crawldb.frontier_queue SET locked_at = NOW() - make_interval(secs => ${lease_ttl} + 5) WHERE url = '${lease_url}';" >/dev/null

http_get "${MANAGER_BASE_URL}/api/frontier/status" >/dev/null
lease_state="$(db_scalar "SELECT state::text FROM crawldb.frontier_queue WHERE url = '${lease_url}' LIMIT 1;")"

stale_complete_resp="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/complete" "{\"workerId\":9201,\"url\":\"${lease_url}\",\"leaseToken\":\"${claim_one_token}\",\"status\":\"completed\",\"daemonId\":\"itest-lease\"}")"
stale_complete_ok="$(json_path_get "${stale_complete_resp}" "data.completed" "false")"
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

echo "[4/6] Concurrent ingest dedupe race"
ingest_root="https://${INGEST_HOST}/root"
ingest_d1="https://${INGEST_HOST}/doc/a"
ingest_d2="https://${INGEST_HOST}/doc/b"

ingest_scope_sql=$(cat <<SQL
INSERT INTO manager.global_setting (key, value, updated_by)
VALUES (
  'crawler.global_config',
  jsonb_build_object(
    'seedEntries', jsonb_build_array(
      jsonb_build_object('url', 'https://${INGEST_HOST}/', 'enabled', true, 'label', 'itest-ingest')
    ),
    'seedUrlsText', '',
    'relevanceAllowedDomainSuffixesText', ''
  ),
  'integration-smoke'
)
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    updated_at = now(),
    updated_by = EXCLUDED.updated_by;
SQL
)
project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -c "${ingest_scope_sql}" >/dev/null

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

echo "[5/6] Seed-domain scope filter (allow subdomains, reject out-of-scope)"
scope_root="https://${BASE_HOST}/scope/root"
scope_same="https://${BASE_HOST}/scope/same"
scope_sub="https://www.${BASE_HOST}/scope/sub"
scope_out="https://${OTHER_HOST}/scope/out"
scope_image_same="https://${BASE_HOST}/img/same.png"
scope_image_sub="https://cdn.${BASE_HOST}/img/sub.png"
scope_image_out="https://${OTHER_HOST}/img/out.png"

global_config_sql=$(cat <<SQL
INSERT INTO manager.global_setting (key, value, updated_by)
VALUES (
  'crawler.global_config',
  jsonb_build_object(
    'seedEntries', jsonb_build_array(
      jsonb_build_object('url', 'https://${BASE_HOST}/', 'enabled', true, 'label', 'itest')
    ),
    'seedUrlsText', '',
    'relevanceAllowedDomainSuffixesText', ''
  ),
  'integration-smoke'
)
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    updated_at = now(),
    updated_by = EXCLUDED.updated_by;
SQL
)
project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -c "${global_config_sql}" >/dev/null

scope_ingest_payload="{\"rawUrl\":\"${scope_root}\",\"discoveredUrls\":[\"${scope_same}\",\"${scope_sub}\",\"${scope_out}\"],\"discoveredImageUrls\":[\"${scope_image_same}\",\"${scope_image_sub}\",\"${scope_image_out}\"],\"downloadResult\":{\"finalUrl\":\"${scope_root}\",\"statusCode\":200,\"pageTypeCode\":\"HTML\",\"htmlContent\":\"<html><body>scope-${TEST_RUN_ID}</body></html>\"}}"
http_post_json "${MANAGER_BASE_URL}/api/crawler/ingest" "${scope_ingest_payload}" >/dev/null

scope_frontier_in_count="$(db_scalar "SELECT COUNT(*) FROM crawldb.frontier_queue WHERE url IN ('${scope_same}','${scope_sub}');")"
scope_frontier_out_count="$(db_scalar "SELECT COUNT(*) FROM crawldb.frontier_queue WHERE url = '${scope_out}';")"
assert_eq "${scope_frontier_in_count}" "2" "in-scope and subdomain discovered URLs should be queued"
assert_eq "${scope_frontier_out_count}" "0" "out-of-scope discovered URL must not be queued"

scope_page_id="$(db_scalar "SELECT id FROM crawldb.page WHERE url = '${scope_root}' LIMIT 1;")"
assert_nonempty "${scope_page_id}" "scope root page id missing"
scope_image_in_count="$(db_scalar "SELECT COUNT(*) FROM crawldb.image WHERE page_id = ${scope_page_id} AND filename IN ('${scope_image_same}','${scope_image_sub}');")"
scope_image_out_count="$(db_scalar "SELECT COUNT(*) FROM crawldb.image WHERE page_id = ${scope_page_id} AND filename = '${scope_image_out}';")"
assert_eq "${scope_image_in_count}" "2" "in-scope and subdomain discovered images should be recorded"
assert_eq "${scope_image_out_count}" "0" "out-of-scope discovered image must not be recorded"

echo "[6/6] Cooldown claim diagnostics (strict wait/retry)"
cool_url_a="https://${BASE_HOST}/cooldown/a"
cool_url_b="https://${BASE_HOST}/cooldown/b"
project_compose exec -T db psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -c "DELETE FROM crawldb.frontier_queue WHERE state = 'QUEUED'::crawldb.frontier_queue_state;" >/dev/null
http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${cool_url_a}\",\"priority\":999000,\"depth\":0}" >/dev/null
http_post_json "${MANAGER_BASE_URL}/api/frontier/seed" "{\"url\":\"${cool_url_b}\",\"priority\":998000,\"depth\":0}" >/dev/null

cool_claim_1="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/claim" '{"workerId":9301,"daemonId":"itest-cooldown"}')"
cool_claim_1_ok="$(json_path_get "${cool_claim_1}" "data.claimed" "false")"
assert_eq "${cool_claim_1_ok}" "true" "first cooldown claim should succeed"

cool_claim_2="$(http_post_json "${MANAGER_BASE_URL}/api/frontier/claim" '{"workerId":9302,"daemonId":"itest-cooldown"}')"
cool_claim_2_ok="$(json_path_get "${cool_claim_2}" "data.claimed" "false")"
cool_blocked="$(json_path_get "${cool_claim_2}" "data.blockedByCooldown" "false")"
cool_retry_ms="$(json_path_get "${cool_claim_2}" "data.retryAfterMilliseconds" "0")"
assert_eq "${cool_claim_2_ok}" "false" "second cooldown claim should be deferred"
assert_eq "${cool_blocked}" "true" "deferred claim should report cooldown block"
if [[ "${cool_retry_ms}" -le 0 ]]; then
  echo "ASSERT FAILED: cooldown diagnostics should include retryAfterMilliseconds > 0" >&2
  exit 1
fi

echo "Integration smoke passed (run id: ${TEST_RUN_ID})"
