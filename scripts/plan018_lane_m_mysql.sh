#!/usr/bin/env bash
# Plan 018 Lane M — disposable MySQL real-store oracle (V5-3).
#
# Recipe authority (do not invent env):
#   - V4-0 forward migrate: evidence/plan018-v4-0-forward-migrate.sidecar.json
#   - DB-backed lane shape: work/state/plan010/within-chat-lane.sh
#   - Full env table: startup/dev/run_full_test_lane.sh
set -euo pipefail

REPO="${REPO:-/home/taishajo/work/NExtSEEK-plan018}"
MYSQL_IMAGE="${MYSQL_IMAGE:-mysql:8.0@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b}"
APP_IMAGE="${APP_IMAGE:-nextseek-nextseek:latest}"
OVERLAY="${OVERLAY:-$REPO/startup/dev/lane_local_settings.py}"
NET="plan018-lane-m-$$"
SIDECAR="plan018-mysql-$$"
PW="${LANE_MYSQL_ROOT_PASSWORD:-plan018lane-$(python3 -c 'import secrets; print(secrets.token_hex(8))')}"
LOG="${LANE_M_LOG:-$REPO/evidence/plan018-v4-8-lane-m.log}"
SIDECAR_OUT="${LANE_M_SIDECAR:-$REPO/evidence/plan018-v4-8-lane-m.sidecar.json}"
PYTEST_TARGET="${LANE_M_PYTEST:-nextseek_api/eval/tests/test_v4_8_mysql.py}"
JUNIT_OUT="${LANE_M_JUNIT:-$REPO/evidence/plan018-v4-8-lane-m.junit.xml}"
SIDECAR_SCHEMA="${LANE_M_SIDECAR_SCHEMA:-plan018-v4-8-lane-m/v1}"

cleanup() {
  docker rm -f "$SIDECAR" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$NET" >/dev/null
docker run -d --name "$SIDECAR" --network "$NET" --network-alias spikemysql \
  -e MYSQL_ROOT_PASSWORD="$PW" \
  "$MYSQL_IMAGE" \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci >/dev/null

for _ in $(seq 1 60); do
  if docker run --rm --network "$NET" "$MYSQL_IMAGE" \
    mysqladmin ping -h spikemysql -uroot -p"$PW" --silent >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

docker run --rm --network "$NET" "$MYSQL_IMAGE" \
  mysql -h spikemysql -uroot -p"$PW" -e \
  "CREATE DATABASE IF NOT EXISTS dmac CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; \
   CREATE DATABASE IF NOT EXISTS seek_production CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

_lane_env=(
  -e PATH=/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  -e PYTHONPATH=/work/chat_nextseek/src:/work
  -e PYTHONDONTWRITEBYTECODE=1
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings_realstack
  -e DJANGO_SECRET_KEY=lane-test-harness-dummy-secret
  -e DJANGO_CSRF_TRUSTED_ORIGINS="http://127.0.0.1:8000 http://localhost:8000"
  -e GCP_API_KEY=SET_IN_LOCAL_ENV
  -e AWS_BEARER_TOKEN_BEDROCK=SET_IN_LOCAL_ENV
  -e FDH_API=SET_IN_LOCAL_ENV
  -e NEXTSEEK_BASE_URL=http://127.0.0.1:8000
  -e CATALOG_FILE=/work/chat_nextseek/agent_model_catalog.json
  -e NEXTSEEK_NEO4J_HOST=neo4j
  -e NEXTSEEK_NEO4J_PASSWORD=placeholder
  -e SEEK_HOST=seek
  -e SEEK_HOSTNAME=http://seek:3000
  -e NEXTSEEK_HOSTNAME=127.0.0.1:8000
  -e SPIKE_DB_HOST=spikemysql
  -e SPIKE_DB_USER=root
  -e SPIKE_DB_PASSWORD="$PW"
  -e SPIKE_DB_PORT=3306
  -e MYSQL_HOST=spikemysql
  -e MYSQL_USER=root
  -e MYSQL_PASSWORD="$PW"
  -e NEXTSEEK_MYSQL_DATABASE=dmac
  -e MYSQL_DATABASE=seek_production
)

: > "$LOG"
{
  echo "REPO=$REPO"
  echo "MYSQL_IMAGE=$MYSQL_IMAGE"
  echo "OVERLAY=$OVERLAY"
  echo "NET=$NET"
  echo "===== migrate --noinput (worktree overlay, leaf through 0017) ====="
} | tee -a "$LOG"

docker run --rm --network "$NET" --entrypoint sh \
  -v "$REPO:/work" \
  -v "$OVERLAY:/work/dmac/local_settings.py:ro" \
  -w /work \
  "${_lane_env[@]}" \
  "$APP_IMAGE" \
  -c 'python manage.py migrate --noinput' 2>&1 | tee -a "$LOG"

{
  echo "===== pytest real-store barrier oracles ====="
} | tee -a "$LOG"

set +o pipefail
docker run --rm --network "$NET" --entrypoint sh \
  -v "$REPO:/work" \
  -v "$OVERLAY:/work/dmac/local_settings.py:ro" \
  -w /work \
  "${_lane_env[@]}" \
  "$APP_IMAGE" \
  -c 'python -m pytest '"$PYTEST_TARGET"' -q --tb=line -p no:cacheprovider --reuse-db --junitxml='"$JUNIT_OUT" \
  2>&1 | tee -a "$LOG"
PYTEST_EXIT=${PIPESTATUS[0]}
set -o pipefail

if [ "$PYTEST_EXIT" -ne 0 ]; then
  echo "Lane M pytest failed with exit $PYTEST_EXIT" | tee -a "$LOG"
  exit "$PYTEST_EXIT"
fi

ISOLATION=$(docker exec "$SIDECAR" mysql -uroot -p"$PW" -N -e "SELECT @@transaction_isolation" 2>/dev/null || echo "unknown")
python3 - <<PY
import json, os, pathlib
repo = pathlib.Path("$REPO")
default_oracles = [
    "nway_contention_multiprocess",
    "idempotency_replay_multiprocess",
    "crash_before_reserve",
    "crash_after_reserve",
    "crash_after_provider",
    "crash_before_reconcile",
    "crash_release_on_provider_exception",
    "broker_redelivery",
    "orphan_release",
    "expiry_sweep",
]
target = os.environ.get("LANE_M_PYTEST", "$PYTEST_TARGET")
if "test_generation_store_mysql" in target:
    default_oracles = [
        "stale_cas",
        "two_activators",
        "parent_mismatch_refused",
        "immutable_overwrite_refused",
        "rollback",
        "reader_single_hash",
        "corruption",
        "payload_canonical_tamper",
        "taxonomy_corpus_incompat",
        "partial_publish_refused",
        "crash_publish_boundary",
        "crash_activation_boundary",
    ]
oracles = json.loads(os.environ.get("LANE_M_ORACLES", json.dumps(default_oracles)))
sidecar = {
    "schema": "$SIDECAR_SCHEMA",
    "gate": "PASS",
    "lane": "M",
    "recipe": "within-chat-lane.sh shape + V4-0 forward-migrate sidecar",
    "settings_module": "dmac.test_settings_realstack",
    "local_settings_overlay": "$OVERLAY",
    "mysql_image": "$MYSQL_IMAGE",
    "isolation_level": "$ISOLATION",
    "pytest_target": "$PYTEST_TARGET",
    "oracles": oracles,
    "paid_or_live_resources_used": False,
}
pathlib.Path("$SIDECAR_OUT").write_text(json.dumps(sidecar, indent=2) + "\n")
PY

echo "Lane M complete — sidecar written"
