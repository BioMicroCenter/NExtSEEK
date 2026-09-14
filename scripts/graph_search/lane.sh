#!/usr/bin/env bash
# Throwaway lane for the graph_search work: a MySQL (the existing scratch container) and a Neo4j,
# both on the gs-net network, and the app image run over a read-only mount of this checkout.
# Secrets and memory caps come from $GS_WORK/lane.env. Secrets are never printed and never put on
# a command line: they reach containers as bare `-e NAME` flags, which docker reads from this
# script's environment. See scripts/graph_search/README.md.
set -euo pipefail
: "${GS_WORK:?set GS_WORK to the graph-search work directory}"
# shellcheck disable=SC1091
source "$GS_WORK/lane.env"
: "${GS_MYSQL_ROOT_PASSWORD:?lane.env must set GS_MYSQL_ROOT_PASSWORD}"
: "${GS_NEO4J_PASSWORD:?lane.env must set GS_NEO4J_PASSWORD}"
MYSQL_C="${GS_MYSQL_CONTAINER:-gs-scratch-devbox-mysql}"
NEO4J_C=gs-v11-neo4j
NEO4J_VOLUME=gs-v11-neo4j-data
NEO4J_IMAGE="${GS_NEO4J_IMAGE:-neo4j:latest}"
NEO4J_MEMORY="${GS_NEO4J_MEMORY:-4g}"
NEO4J_HEAP="${GS_NEO4J_HEAP:-1500m}"
NEO4J_PAGECACHE="${GS_NEO4J_PAGECACHE:-1500m}"
NEO4J_TX_MAX="${GS_NEO4J_TX_MAX:-1g}"
APP_MEMORY="${GS_APP_MEMORY:-4g}"
APP_IMAGE="${GS_APP_IMAGE:-nextseek-nextseek:latest}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

free_check() {
  local avail; avail=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
  echo "MemAvailable ${avail} GiB" >&2
  if (( avail < 2 )); then echo "refusing: less than 2 GiB available" >&2; exit 3; fi
}

neo4j_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$NEO4J_C" 2>/dev/null)" == true ]]
}

# cypher-shell reads NEO4J_USERNAME and NEO4J_PASSWORD from its environment.
cypher() {
  NEO4J_USERNAME=neo4j NEO4J_PASSWORD="$GS_NEO4J_PASSWORD" \
    docker exec -i -e NEO4J_USERNAME -e NEO4J_PASSWORD "$NEO4J_C" cypher-shell "$@"
}

case "${1:-}" in
  net)
    docker network inspect gs-net >/dev/null 2>&1 || docker network create gs-net >/dev/null
    docker network connect gs-net "$MYSQL_C" 2>/dev/null || true
    echo "gs-net ready ($MYSQL_C attached)" ;;
  neo4j-up)
    if neo4j_running; then echo "neo4j already up"; exit 0; fi
    free_check
    docker rm -f "$NEO4J_C" >/dev/null 2>&1 || true
    NEO4J_AUTH="neo4j/${GS_NEO4J_PASSWORD}" \
    docker run -d --name "$NEO4J_C" --network gs-net \
      --memory "$NEO4J_MEMORY" --memory-swap "$NEO4J_MEMORY" \
      -e NEO4J_AUTH \
      -e NEO4J_server_memory_heap_initial__size="$NEO4J_HEAP" \
      -e NEO4J_server_memory_heap_max__size="$NEO4J_HEAP" \
      -e NEO4J_server_memory_pagecache_size="$NEO4J_PAGECACHE" \
      -e NEO4J_db_memory_transaction_max="$NEO4J_TX_MAX" \
      -e NEO4J_db_transaction_timeout=300s \
      -v "$NEO4J_VOLUME":/data "$NEO4J_IMAGE" >/dev/null
    for _ in $(seq 1 100); do
      if cypher 'RETURN 1' >/dev/null 2>&1; then echo "neo4j up"; exit 0; fi
      if ! neo4j_running; then
        echo "neo4j exited during start-up; last log lines:" >&2
        docker logs --tail 30 "$NEO4J_C" >&2 || true
        exit 1
      fi
      sleep 3
    done
    echo "neo4j did not answer within 300 s" >&2; exit 1 ;;
  neo4j-down)
    docker rm -f "$NEO4J_C" >/dev/null 2>&1 || true
    echo "neo4j down (volume $NEO4J_VOLUME kept)" ;;
  neo4j-cypher)
    # neo4j-cypher '<statement>' runs one statement; with no statement (or '-') it reads stdin.
    if [[ $# -ge 2 && "$2" != - ]]; then cypher --format plain "$2"
    else cypher --format plain; fi ;;
  mysql)
    # mysql '<sql>' [mysql client options]; with no SQL (or '-') it reads stdin.
    run=(docker exec -i -e MYSQL_PWD "$MYSQL_C" mysql -uroot --default-character-set=utf8mb4 "${@:3}")
    if [[ $# -ge 2 && "$2" != - ]]; then MYSQL_PWD="$GS_MYSQL_ROOT_PASSWORD" "${run[@]}" <<<"$2"
    else MYSQL_PWD="$GS_MYSQL_ROOT_PASSWORD" "${run[@]}"; fi ;;
  app|python)
    free_check
    mkdir -p "$REPO/schema_rag/duckdb" "$REPO/schema_rag/embedding_models" "$GS_WORK/lane"
    # The stack renders dmac/local_settings.py from startup/templates/local_settings.py.template;
    # a read-only checkout has none, and dmac.settings alone lacks names the URLconf reads at
    # import. This shim (no secrets) supplies inert values for exactly those names.
    cat >"$GS_WORK/lane/gs_lane_settings.py" <<'PY'
# Written by scripts/graph_search/lane.sh on every run; edits here are overwritten.
from dmac.settings import *  # noqa: F401,F403

PUBLISH_URL = SEEK_URL  # noqa: F405 (set by dmac.settings from SEEK_HOST)
ASSISTANT_PARTICIPATING_PROJECTS = set()
PUBLISH_STATS_FILE = "/nonexistent/published_stats.xlsx"
SMART_SEARCH_URL = ""
TEST_CASES = {}
PY
    export MYSQL_PASSWORD="$GS_MYSQL_ROOT_PASSWORD" NEXTSEEK_NEO4J_PASSWORD="$GS_NEO4J_PASSWORD"
    run=(docker run --rm -i --name "gs-app-$$" --network gs-net
      --memory "$APP_MEMORY" --memory-swap "$APP_MEMORY"
      -v "$REPO":/src:ro -v "$GS_WORK":/gswork -w /src
      -e DJANGO_SETTINGS_MODULE=gs_lane_settings -e LOG_DIR=/tmp/nextseek-logs -e PYTHONDONTWRITEBYTECODE=1
      -e PYTHONPATH=/src:/gswork/lane
      -e SEEK_HOST=gs-no-seek -e SEEK_HOSTNAME=http://gs-no-seek:3000 -e NEXTSEEK_HOSTNAME=127.0.0.1:8000
      -e DJANGO_SECRET_KEY=gs-lane-inert -e DJANGO_CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000
      -e MYSQL_HOST="$MYSQL_C" -e MYSQL_USER=root -e MYSQL_PASSWORD
      -e MYSQL_DATABASE=seek_production -e NEXTSEEK_MYSQL_DATABASE=dmac
      -e NEXTSEEK_NEO4J_HOST="$NEO4J_C" -e NEXTSEEK_NEO4J_PASSWORD
      -e GS_RUN_DIR=/gswork/runs "$APP_IMAGE")
    if [[ "$1" == app ]]; then "${run[@]}" /app/.venv/bin/python manage.py "${@:2}"
    else "${run[@]}" /app/.venv/bin/python "${@:2}"; fi ;;
  free-check) free_check ;;
  *) echo "usage: lane.sh net|neo4j-up|neo4j-down|neo4j-cypher|mysql|app|python|free-check" >&2; exit 2 ;;
esac
