#!/bin/bash
# run_full_test_lane.sh — the single reproducible entry point for the full
# NExtSEEK base test lane (UD-12 shape, plan base-suite-green).
#
# PURPOSE
#   Run the frozen selection `nextseek_api/tests startup/tests` in ONE pytest
#   invocation under `dmac.test_settings_realstack` (full dmac.settings + fast
#   password hasher), inside the pinned app image, against a disposable
#   digest-pinned mysql:8.0 sidecar, on a docker network created with
#   `--internal` — the lane has NO WAN egress BY CONSTRUCTION. This script is
#   the productionized form of the measured lane recorded in
#   REALSTACK-FULL-RUN-2026-07-22.log and is the exact artifact the lane
#   contract freezes (lane identity = this script + image digests + embedding
#   model revision/manifest).
#
# USAGE
#   startup/dev/run_full_test_lane.sh <test-tree>
#
#   <test-tree>  REQUIRED. Absolute or relative path to the checkout whose
#                tests run — it is mounted at /work inside the pytest
#                container. It must already have its embedding-model cache
#                provisioned (see PRE-FLIGHT below).
#
#   The local_settings overlay (lane_local_settings.py), the model manifest
#   (bge-small-en-v1.5.manifest.json), and the provisioning/verify script
#   (provision_embedding_model.sh) are resolved from THIS SCRIPT'S OWN repo
#   tree (dirname-based), NOT from <test-tree> — the test tree may be an
#   older checkout that does not contain them.
#
#   Optional env override:
#     LANE_MYSQL_ROOT_PASSWORD  sidecar root password. Default: generated
#                               per-run from /dev/urandom. NEVER hardcoded;
#                               must be non-empty (an empty string is falsy in
#                               the migration tests' _HAS_DB_FIXTURE gate and
#                               silently disables the 13 MySQL heal tests).
#
# PRE-FLIGHT (all BEFORE any test runs; each failure exits nonzero)
#   1. App image identity: `docker image inspect nextseek-nextseek:latest`
#      must report exactly the pinned ID
#      sha256:397ca26e65051d05693330893898cb6b0b0fd4d430cc89df0b32fdd223f15ee4.
#   2. Embedding model cache (UD-13): <test-tree>/schema_rag/embedding_models
#      is manifest-verified via `provision_embedding_model.sh --verify`.
#      On absence/mismatch the lane FAILS FAST and tells you to run
#      startup/dev/provision_embedding_model.sh — the lane itself NEVER
#      touches the network to fetch the model (HF_HUB_OFFLINE=1 +
#      TRANSFORMERS_OFFLINE=1 are exported in the pytest container).
#      NOTE: provisioning is once per CHECKOUT, not per machine — the cache
#      dir schema_rag/embedding_models is gitignored, so every fresh
#      clone/worktree starts empty and must be provisioned before this lane.
#
# ENVIRONMENT PASSED TO THE PYTEST CONTAINER (exactly the measured run's env
# plus the two offline switches; overlay mounted read-only over
# /work/dmac/local_settings.py)
#   | Variable                  | Value                                   | Why |
#   |---------------------------|-----------------------------------------|-----|
#   | PATH                      | /app/.venv/bin first                    | image venv python/pytest |
#   | PYTHONDONTWRITEBYTECODE   | 1                                       | no .pyc litter in the mounted tree |
#   | DJANGO_SETTINGS_MODULE    | dmac.test_settings_realstack            | UD-12 lane settings module |
#   | DJANGO_SECRET_KEY         | lane-test-harness-dummy-secret          | required at import; dummy, not a secret |
#   | GCP_API_KEY               | SET_IN_LOCAL_ENV (dummy)                | import-time presence only; never called |
#   | AWS_BEARER_TOKEN_BEDROCK  | SET_IN_LOCAL_ENV (dummy)                | import-time presence only; never called |
#   | FDH_API                   | SET_IN_LOCAL_ENV (dummy)                | import-time presence only; never called |
#   | NEXTSEEK_BASE_URL         | http://127.0.0.1:8000                   | ChatConfig construction |
#   | CATALOG_FILE              | /work/chat_nextseek/agent_model_catalog.json | catalog inside the mounted tree |
#   | NEXTSEEK_NEO4J_HOST       | neo4j (no such host in the lane)        | settings import; no graph calls |
#   | NEXTSEEK_NEO4J_PASSWORD   | placeholder                             | settings import |
#   | SEEK_HOST                 | seek                                    | settings import |
#   | SEEK_HOSTNAME             | http://seek:3000                        | settings import |
#   | NEXTSEEK_HOSTNAME         | 127.0.0.1:8000                          | settings import |
#   | SPIKE_DB_HOST/PORT        | spikemysql / 3306                       | migration-0007 heal tests → sidecar |
#   | SPIKE_DB_USER/PASSWORD    | root / <generated per-run>              | sidecar credentials |
#   | MYSQL_HOST/USER/PASSWORD  | spikemysql / root / <generated per-run> | sidecar credentials |
#   | NEXTSEEK_MYSQL_DATABASE   | dmac                                    | app schema name |
#   | MYSQL_DATABASE            | seek_production                         | SEEK schema name |
#   | HF_HUB_OFFLINE            | 1                                       | model MUST come from the provisioned cache |
#   | TRANSFORMERS_OFFLINE      | 1                                       | ditto — zero network by construction |
#
# EXACT TEST INVOCATION (frozen; no -k, no --ignore, no deselection)
#   python -m pytest -q --tb=line -rsf -p no:cacheprovider nextseek_api/tests startup/tests
#
# TEARDOWN GUARANTEES
#   - trap-based cleanup runs on ANY exit (normal, error, or signal): the
#     mysql sidecar container and the lane network are force-removed.
#   - the pytest container and the readiness-poll containers are `--rm`.
#   - the script's exit code is the pytest container's exit code (0 = green,
#     1 = test failures, 124 = the 2200s watchdog fired), except for the
#     documented pre-flight/bring-up codes below.
#   - fully self-contained: creates its own internal network + disposable
#     sidecar with unique per-PID names; never touches any running compose
#     project, its containers, volumes, or networks.
#
# EXIT CODES
#   2   usage error (missing/invalid <test-tree>)
#   3   pre-flight failure (image identity mismatch, or model cache
#       absent/mismatched — run startup/dev/provision_embedding_model.sh)
#   65  lane network creation failed
#   66  mysql sidecar failed to start
#   67  mysql sidecar never became ready (mysqladmin ping poll exhausted)
#   *   otherwise: the pytest container's exit code, passed through

set -u

# ---------------------------------------------------------------------------
# Pinned identities
# ---------------------------------------------------------------------------
APP_IMAGE="nextseek-nextseek:latest"
APP_IMAGE_ID_PIN="sha256:66d06207ab7b04886c5129f553302566dd83ee8318a325e1308367ebcf8b64d2"
MYSQL_IMAGE="mysql:8.0@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b"

# ---------------------------------------------------------------------------
# Resolve lane inputs from THIS script's own tree (dirname-based)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY="${SCRIPT_DIR}/lane_local_settings.py"
PROVISION="${SCRIPT_DIR}/provision_embedding_model.sh"

# ---------------------------------------------------------------------------
# Argument: the TEST TREE (mounted at /work)
# ---------------------------------------------------------------------------
if [ $# -ne 1 ]; then
    echo "usage: $0 <test-tree>" >&2
    echo "  <test-tree>: checkout to test (mounted at /work in the lane)" >&2
    exit 2
fi
if [ ! -d "$1" ]; then
    echo "ERROR: test tree not found or not a directory: $1" >&2
    exit 2
fi
TEST_TREE="$(cd "$1" && pwd)"
for required in dmac nextseek_api/tests startup/tests; do
    if [ ! -e "${TEST_TREE}/${required}" ]; then
        echo "ERROR: ${TEST_TREE} does not look like a NExtSEEK checkout (missing ${required})" >&2
        exit 2
    fi
done
for lane_input in "${OVERLAY}" "${PROVISION}"; do
    if [ ! -f "${lane_input}" ]; then
        echo "ERROR: lane input missing from the script's own tree: ${lane_input}" >&2
        exit 2
    fi
done

# ---------------------------------------------------------------------------
# Pre-flight 1 — app image identity (hard assert BEFORE any test runs)
# ---------------------------------------------------------------------------
ACTUAL_IMAGE_ID="$(docker image inspect "${APP_IMAGE}" --format '{{.Id}}' 2>/dev/null)"
if [ -z "${ACTUAL_IMAGE_ID}" ]; then
    echo "ERROR: pre-flight: image ${APP_IMAGE} not present" >&2
    exit 3
fi
if [ "${ACTUAL_IMAGE_ID}" != "${APP_IMAGE_ID_PIN}" ]; then
    echo "ERROR: pre-flight: ${APP_IMAGE} identity mismatch" >&2
    echo "  expected: ${APP_IMAGE_ID_PIN}" >&2
    echo "  actual:   ${ACTUAL_IMAGE_ID}" >&2
    exit 3
fi
echo "pre-flight: app image OK (${ACTUAL_IMAGE_ID})"

# ---------------------------------------------------------------------------
# Pre-flight 2 — embedding model cache manifest-verified (UD-13)
# ---------------------------------------------------------------------------
MODEL_CACHE="${TEST_TREE}/schema_rag/embedding_models"
if ! bash "${PROVISION}" --verify "${MODEL_CACHE}"; then
    echo "ERROR: pre-flight: embedding model cache at ${MODEL_CACHE} is absent or" >&2
    echo "  does not match the manifest. The lane NEVER downloads the model." >&2
    echo "  Provision this checkout first (once per CHECKOUT):" >&2
    echo "    startup/dev/provision_embedding_model.sh --target ${TEST_TREE} [--seed <local-verified-cache>]" >&2
    exit 3
fi
echo "pre-flight: embedding model cache OK (${MODEL_CACHE})"

# ---------------------------------------------------------------------------
# Sidecar password — generated per-run (or caller-supplied); NEVER hardcoded,
# NEVER empty (empty is falsy in the _HAS_DB_FIXTURE gate).
# ---------------------------------------------------------------------------
PW="${LANE_MYSQL_ROOT_PASSWORD:-}"
if [ -z "${PW}" ]; then
    PW="lane-$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
if [ -z "${PW}" ]; then
    echo "ERROR: sidecar password is empty — refusing to run (would silently skip the MySQL heal tests)" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Lane bring-up: internal-only network + disposable pinned mysql sidecar
# ---------------------------------------------------------------------------
NET="testlane-net-$$"
SIDECAR="testlane-mysql-$$"
PYTEST_CTR="testlane-pytest-$$"

cleanup() {
    # Remove the named pytest container first: if the 2200s watchdog fires it
    # kills the `docker run` client but leaves the container running, which in
    # turn blocks `docker network rm` (network still attached). Force-remove it
    # explicitly so teardown always completes (plan-009 residual-debt item 1).
    docker rm -f "${PYTEST_CTR}" >/dev/null 2>&1
    docker rm -f "${SIDECAR}" >/dev/null 2>&1
    docker network rm "${NET}" >/dev/null 2>&1
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

docker network create --internal "${NET}" >/dev/null || {
    echo "ERROR: could not create internal lane network ${NET}" >&2
    exit 65
}

docker run -d --name "${SIDECAR}" --network "${NET}" --network-alias spikemysql \
    -e MYSQL_ROOT_PASSWORD="${PW}" \
    "${MYSQL_IMAGE}" \
    --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci >/dev/null || {
    echo "ERROR: mysql sidecar failed to start" >&2
    exit 66
}

# Readiness: poll mysqladmin ping (never sleep-guess). 60 tries x 2s = 120s cap.
READY=0
for _try in $(seq 1 60); do
    if docker run --rm --network "${NET}" "${MYSQL_IMAGE}" \
        mysqladmin ping -h spikemysql -u root -p"${PW}" --silent >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 2
done
if [ "${READY}" -ne 1 ]; then
    echo "ERROR: mysql sidecar never answered mysqladmin ping" >&2
    exit 67
fi
echo "lane: sidecar ready on ${NET} (alias spikemysql)"

# ---------------------------------------------------------------------------
# The single pytest invocation (frozen selection; overlay mounted read-only)
# ---------------------------------------------------------------------------
timeout 2200 docker run --rm --name "${PYTEST_CTR}" --network "${NET}" --entrypoint sh \
    -v "${TEST_TREE}:/work" \
    -v "${OVERLAY}:/work/dmac/local_settings.py:ro" \
    -w /work \
    -e PATH=/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e DJANGO_SETTINGS_MODULE=dmac.test_settings_realstack \
    -e DJANGO_SECRET_KEY=lane-test-harness-dummy-secret \
    -e GCP_API_KEY=SET_IN_LOCAL_ENV -e AWS_BEARER_TOKEN_BEDROCK=SET_IN_LOCAL_ENV -e FDH_API=SET_IN_LOCAL_ENV \
    -e NEXTSEEK_BASE_URL=http://127.0.0.1:8000 -e CATALOG_FILE=/work/chat_nextseek/agent_model_catalog.json \
    -e NEXTSEEK_NEO4J_HOST=neo4j -e NEXTSEEK_NEO4J_PASSWORD=placeholder \
    -e SEEK_HOST=seek -e SEEK_HOSTNAME=http://seek:3000 -e NEXTSEEK_HOSTNAME=127.0.0.1:8000 \
    -e SPIKE_DB_HOST=spikemysql -e SPIKE_DB_USER=root -e SPIKE_DB_PASSWORD="${PW}" -e SPIKE_DB_PORT=3306 \
    -e MYSQL_HOST=spikemysql -e MYSQL_USER=root -e MYSQL_PASSWORD="${PW}" \
    -e NEXTSEEK_MYSQL_DATABASE=dmac -e MYSQL_DATABASE=seek_production \
    -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
    "${APP_IMAGE}" \
    -c 'python -m pytest -q --tb=line -rsf -p no:cacheprovider nextseek_api/tests startup/tests'
PYTEST_RC=$?
echo "lane: pytest container exit code ${PYTEST_RC}"
exit "${PYTEST_RC}"
