#!/usr/bin/env bash
# Dump the lane's v1.1 graph to an offline neo4j-admin archive, for the operator's load into the live stack (plan
# task L1). A database cannot be dumped while a server mounts it, so this stops gs-v11-neo4j, dumps the
# gs-v11-neo4j-data volume from a throwaway container, and starts gs-v11-neo4j again, also when the dump fails.
#
#   GS_WORK=<work dir> scripts/graph_search/dump_graph.sh [destination dir]
#
# The default destination is $GS_WORK/seeds/v11-graph-2026-09-14. It receives:
#   neo4j.dump   the archive of database neo4j (load it with `neo4j-admin database load`)
#   counts.txt   node count per label and relationship count per type, read before the stop
#   info.txt     `neo4j-admin database load --info` on the finished archive, which proves it reads back
#   dump.log     neo4j-admin's messages
#   SHA256SUMS
# The graph holds real sample metadata, so the directory is mode 700, its files 600, and none of it enters the
# repository. neo4j-admin runs as the image's neo4j user and writes to stdout; the host shell writes every file, so
# nothing on the host is owned by a container user.
set -euo pipefail
: "${GS_WORK:?set GS_WORK to the graph-search work directory}"
# shellcheck disable=SC1091
source "$GS_WORK/lane.env"
HERE="$(cd "$(dirname "$0")" && pwd)"
NEO4J_C=gs-v11-neo4j
NEO4J_VOLUME=gs-v11-neo4j-data
NEO4J_IMAGE="${GS_NEO4J_IMAGE:-neo4j:latest}"
DUMP_MEMORY="${GS_DUMP_MEMORY:-1g}"
DEST="${1:-$GS_WORK/seeds/v11-graph-2026-09-14}"
DATABASE=neo4j

running() { [[ "$(docker inspect -f '{{.State.Running}}' "$NEO4J_C" 2>/dev/null)" == true ]]; }

# cypher-shell runs in its own throwaway container on gs-net. `docker exec` into gs-v11-neo4j would start a second
# JVM inside that container's memory cap, and with the v1.1 graph loaded that gets the container OOM-killed.
cypher() {
  NEO4J_USERNAME=neo4j NEO4J_PASSWORD="$GS_NEO4J_PASSWORD" \
    docker run --rm -i --network gs-net --memory 512m --memory-swap 512m -e NEO4J_USERNAME -e NEO4J_PASSWORD \
      --entrypoint cypher-shell "$NEO4J_IMAGE" -a "neo4j://$NEO4J_C:7687" --format plain "$@"
}

was_running=false
restart() {
  local rc=$?
  trap - EXIT
  if $was_running && ! running; then
    docker start "$NEO4J_C" >/dev/null
    local up=false
    for _ in $(seq 1 100); do
      if cypher 'RETURN 1' >/dev/null 2>&1; then up=true; break; fi
      sleep 3
    done
    if $up; then echo "$NEO4J_C started again"
    else echo "$NEO4J_C did not answer within 300 s of its restart" >&2; rc=1; fi
  fi
  exit "$rc"
}
trap restart EXIT

umask 077
mkdir -p "$DEST"
chmod 700 "$DEST"
rm -f "$DEST/neo4j.dump.partial"

# neo4j-admin runs as the image's own neo4j user, which owns the volume's files.
NEO4J_UID="$(docker run --rm --network none --memory 256m --entrypoint id "$NEO4J_IMAGE" -u neo4j)"
NEO4J_GID="$(docker run --rm --network none --memory 256m --entrypoint id "$NEO4J_IMAGE" -g neo4j)"
admin=(docker run --rm -i --network none --memory "$DUMP_MEMORY" --memory-swap "$DUMP_MEMORY"
  --user "$NEO4J_UID:$NEO4J_GID" --entrypoint neo4j-admin)

if running; then
  was_running=true
  "$HERE/lane.sh" neo4j-cypher "MATCH (n) UNWIND labels(n) AS name RETURN 'node' AS kind, name, count(*) AS n
UNION ALL MATCH ()-[r]->() RETURN 'relationship' AS kind, type(r) AS name, count(r) AS n" >"$DEST/counts.txt"
  echo "stopping $NEO4J_C"
  docker stop -t 300 "$NEO4J_C" >/dev/null
else
  echo "$NEO4J_C is not running: dumping the volume as it is, without counts" >&2
  rm -f "$DEST/counts.txt"
fi

echo "dumping database $DATABASE from volume $NEO4J_VOLUME"
started=$SECONDS
"${admin[@]}" --name gs-v11-dump -v "$NEO4J_VOLUME":/data "$NEO4J_IMAGE" \
  database dump "$DATABASE" --to-stdout >"$DEST/neo4j.dump.partial" 2>"$DEST/dump.log"
echo "dump took $((SECONDS - started)) s"

# The archive must read back before it replaces an earlier one.
"${admin[@]}" --name gs-v11-dump-info "$NEO4J_IMAGE" \
  database load --info --from-stdin "$DATABASE" <"$DEST/neo4j.dump.partial" >"$DEST/info.txt" 2>>"$DEST/dump.log"
mv -f "$DEST/neo4j.dump.partial" "$DEST/neo4j.dump"
(cd "$DEST" && sha256sum neo4j.dump $([[ -f counts.txt ]] && echo counts.txt) >SHA256SUMS)
chmod 600 "$DEST"/*
ls -l "$DEST"
cat "$DEST/info.txt"
