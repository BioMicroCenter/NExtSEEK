#!/bin/bash
# lane.sh <exported tree> <outdir> [extra pytest arguments, e.g. -k prover]
#
# The graph scope lane (spec docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 11.2): a private,
# throwaway Neo4j on its own network, and pytest over NessieAI/tests/chat_nextseek/graph_scope/ in the app image
# against it. Nothing else is touched: the database and the network are uniquely named and removed on every exit.
#
# - Exits 0 with SKIP when docker is not available.
# - Starts the locally present neo4j:latest (--pull never), --memory 1536m, a 512m heap, a 128m page cache, a random
#   password and no plugins, and waits (bounded) for it to start.
# - Runs pytest in nextseek-nextseek:latest (--memory 1536m) over a read-only mount of the exported tree, with
#   GRAPH_SCOPE_NEO4J_URI and GRAPH_SCOPE_NEO4J_PASSWORD set. The output lands in <outdir>/pytest-graph-scope.txt.
#
# Run it on an exported tree (git archive), never on a worktree mount, and through the workstation's one-heavy-step
# wrapper when there is one.
set -u

TREE=${1:?usage: lane.sh <exported tree> <outdir> [pytest args]}
OUT=${2:?usage: lane.sh <exported tree> <outdir> [pytest args]}
shift 2
NEO4J_IMAGE=${GRAPH_SCOPE_NEO4J_IMAGE:-neo4j:latest}
APP_IMAGE=${GRAPH_SCOPE_APP_IMAGE:-nextseek-nextseek:latest}
START_WAIT_S=${GRAPH_SCOPE_START_WAIT_S:-240}

if ! docker info >/dev/null 2>&1; then
  echo "SKIP: docker is not available; the graph scope lane did not run"
  exit 0
fi

TREE=$(cd "$TREE" && pwd) || exit 2
mkdir -p "$OUT" || exit 2
OUT=$(cd "$OUT" && pwd) || exit 2
mkdir -p "$TREE/schema_rag/duckdb" "$TREE/schema_rag/embedding_models" || {
  echo "the tree must be writable so the schema_rag directories exist before the read-only mount" >&2
  exit 2
}

SUFFIX="$(date +%s)-$$-$RANDOM"
NET="graph-scope-net-$SUFFIX"
DB="graph-scope-neo4j-$SUFFIX"
RUNNER="graph-scope-pytest-$SUFFIX"
PASSWORD="gs$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"

cleanup() {
  docker rm -f "$RUNNER" >/dev/null 2>&1
  docker rm -f "$DB" >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
}
trap cleanup EXIT
trap 'exit 130' INT TERM

docker network create "$NET" >/dev/null || exit 1
docker run -d --pull never --name "$DB" --network "$NET" --memory 1536m \
  -e NEO4J_AUTH="neo4j/$PASSWORD" \
  -e NEO4J_server_memory_heap_initial__size=512m \
  -e NEO4J_server_memory_heap_max__size=512m \
  -e NEO4J_server_memory_pagecache_size=128m \
  "$NEO4J_IMAGE" >/dev/null || exit 1

ready=0
for _ in $(seq 1 $((START_WAIT_S / 2))); do
  if docker logs "$DB" 2>&1 | grep -q "Started\."; then
    ready=1
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$DB" 2>/dev/null)" != "true" ]; then
    break
  fi
  sleep 2
done
if [ "$ready" != 1 ]; then
  echo "Neo4j did not start within ${START_WAIT_S}s" >&2
  docker logs "$DB" 2>&1 | tail -20 > "$OUT/neo4j-start.txt"
  exit 1
fi

docker run --rm --name "$RUNNER" --network "$NET" --memory 1536m \
  -e LOG_DIR=/tmp/nextseek-logs -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/src/NessieAI/chat_nextseek/src \
  -e GRAPH_SCOPE_NEO4J_URI="bolt://$DB:7687" -e GRAPH_SCOPE_NEO4J_PASSWORD="$PASSWORD" \
  -v "$TREE":/src:ro -w /src "$APP_IMAGE" \
  /app/.venv/bin/python -m pytest NessieAI/tests/chat_nextseek/graph_scope/ -q -rA -p no:cacheprovider "$@" \
  > "$OUT/pytest-graph-scope.txt" 2>&1
rc=$?
echo "graph-scope exit=$rc summary: $(tail -1 "$OUT/pytest-graph-scope.txt")" | tee "$OUT/exit-graph-scope.txt"
exit $rc
