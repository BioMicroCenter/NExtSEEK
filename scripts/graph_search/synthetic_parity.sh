#!/bin/bash
# synthetic_parity.sh <exported tree> <out dir>
#
# Runs scripts/graph_search/synthetic_parity.py (its docstring says what it proves) in a throwaway lane: an empty
# MySQL 8.0 and an empty Neo4j on a private, internal network, each memory-capped, and the app image over the exported
# tree, read-only. Every container and the network are removed on exit, whatever happens. The tree must be an export
# (git archive), never a worktree. Run it through the heavy-step serializer when one is in use.
#
# Passwords are generated per run and reach the containers only through the environment (-e NAME), never argv.
set -euo pipefail
TREE=$(realpath "$1")
OUT=$(realpath -m "$2")
mkdir -p "$OUT"
RUN=$$
NET=gs-synth-net-$RUN
MYSQL=gs-synth-mysql-$RUN
NEO4J=gs-synth-neo4j-$RUN
APP=gs-synth-app-$RUN
MYSQL_IMAGE=${GS_SYNTH_MYSQL_IMAGE:-mysql:8.0}
NEO4J_IMAGE=${GS_SYNTH_NEO4J_IMAGE:-neo4j:latest}
APP_IMAGE=${GS_SYNTH_APP_IMAGE:-nextseek-nextseek:latest}

cleanup() {
  docker rm -f "$APP" "$MYSQL" "$NEO4J" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

export MYSQL_ROOT_PASSWORD GS_SYNTH_MYSQL_PASSWORD GS_SYNTH_NEO4J_PASSWORD NEO4J_AUTH
MYSQL_ROOT_PASSWORD=$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')
GS_SYNTH_MYSQL_PASSWORD=$MYSQL_ROOT_PASSWORD
GS_SYNTH_NEO4J_PASSWORD=$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')
NEO4J_AUTH=neo4j/$GS_SYNTH_NEO4J_PASSWORD

docker network create --internal "$NET" >/dev/null
docker run -d --name "$MYSQL" --network "$NET" --memory 1024m --memory-swap 1024m \
  -e MYSQL_ROOT_PASSWORD -e MYSQL_DATABASE=seek_production "$MYSQL_IMAGE" \
  --innodb-buffer-pool-size=64M --performance-schema=OFF --skip-log-bin \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci >/dev/null
docker run -d --name "$NEO4J" --network "$NET" --memory 1536m --memory-swap 1536m \
  -e NEO4J_AUTH -e NEO4J_server_memory_heap_initial__size=512m -e NEO4J_server_memory_heap_max__size=512m \
  -e NEO4J_server_memory_pagecache_size=256m -e NEO4J_db_memory_transaction_max=256m "$NEO4J_IMAGE" >/dev/null

set +e
docker run --rm --name "$APP" --network "$NET" --memory 1536m --memory-swap 1536m \
  -e GS_SYNTH_MYSQL_PASSWORD -e GS_SYNTH_NEO4J_PASSWORD \
  -e GS_SYNTH_MYSQL_HOST="$MYSQL" -e GS_SYNTH_NEO4J_HOST="$NEO4J" \
  -e LOG_DIR=/tmp/nextseek-logs -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/src/NessieAI/chat_nextseek/src \
  -v "$TREE":/src:ro -v "$OUT":/out -w /src "$APP_IMAGE" \
  /app/.venv/bin/python scripts/graph_search/synthetic_parity.py --out-dir /out "${@:3}"
status=$?
set -e
docker run --rm --network none --memory 256m -v "$OUT":/out "$APP_IMAGE" \
  chown -R "$(id -u):$(id -g)" /out >/dev/null 2>&1 || true
exit $status
