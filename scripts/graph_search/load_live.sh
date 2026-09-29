#!/usr/bin/env bash
# Plan task L1, OPERATOR-RUN: load the merged MySQL and the v1.1 graph into the LIVE local stack for the benchmark,
# and put the previous live data back afterwards. Every step that changes the live stack needs --yes.
#
#   GS_WORK=<work dir> scripts/graph_search/load_live.sh preflight
#   GS_WORK=<work dir> scripts/graph_search/load_live.sh snapshot --yes   # live data -> $GS_WORK/seeds/live-pre-l1/
#   GS_WORK=<work dir> scripts/graph_search/load_live.sh load --yes       # merged MySQL + v1.1 graph -> live stack
#   GS_WORK=<work dir> scripts/graph_search/load_live.sh verify           # counts + graph_sync --verify (branch deployed)
#   GS_WORK=<work dir> scripts/graph_search/load_live.sh restore --yes    # the snapshot -> live stack
#
# What `load` changes:
# - MySQL seek_production: the merged core dump replaces every core table (DROP, CREATE, INSERT per table). The
#   *_auth_lookup tables are not in the dump and stay as they are, so TCGA samples get no SEEK permission rows;
#   SEEK's own pages are wrong for them until `restore`.
# - MySQL dmac: only the rows the merge added are inserted (INSERT IGNORE into five tables), so the live dmac's other
#   rows, chats and tasks included, are left alone.
# - Neo4j: the live container is stopped, database neo4j is replaced offline with neo4j-admin, and it is started again.
# Keep local Nessie off while the v1.1 graph is loaded: its schema fetch does not understand it, and its graph tool
# is unscoped.
#
# The live containers are found by name (seek-mysql, neo4j, nextseek) and the Neo4j data volume is neo4j-data.
# Passwords come from each container's own environment and never appear on a command line.
set -euo pipefail
: "${GS_WORK:?set GS_WORK to the graph-search work directory}"
MERGED="$GS_WORK/seeds/merged-2026-09-14"
GRAPH="$GS_WORK/seeds/v11-graph-2026-09-14"
SNAP="$GS_WORK/seeds/live-pre-l1"
RUNS="$GS_WORK/runs/L1"
MYSQL_C=seek-mysql
NEO4J_C=neo4j
NEO4J_VOLUME=neo4j-data
APP_C=nextseek
ADMIN_MEMORY="${GS_ADMIN_MEMORY:-2g}"
MIN_FREE_GIB=4
MIN_DISK_GB=40
DMAC_TABLES=(assays_internal_assays auth_user internal_assays sample_types_clades seek_user_profile)
EXPECTED_SAMPLES=1084754

die() { echo "load_live: $*" >&2; exit 1; }
say() { echo "== $*"; }
has_flag() { local f=$1; shift; for a in "$@"; do [[ "$a" == "$f" ]] && return 0; done; return 1; }
need_yes() { has_flag --yes "$@" || die "this step changes the live stack; rerun with --yes"; }

live_mysql() {
  docker exec -i "$MYSQL_C" sh -c \
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot --default-character-set=utf8mb4 "$@"' live-mysql "$@"
}
live_mysqldump() {
  docker exec -i "$MYSQL_C" sh -c \
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --routines --triggers --default-character-set=utf8mb4 "$@"' \
    live-mysqldump "$@"
}
# The live Neo4j has no memory cap, so a cypher-shell exec inside it is safe (unlike the capped lane container).
cypher_live() {
  docker exec -i "$NEO4J_C" sh -c \
    'NEO4J_USERNAME=neo4j NEO4J_PASSWORD="${NEO4J_AUTH#neo4j/}" exec cypher-shell --format plain "$@"' cs "$@"
}
running() { [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" == true ]]; }

neo4j_image() { docker inspect -f '{{.Image}}' "$NEO4J_C"; }
admin() {
  local img uid gid
  img="$(neo4j_image)"
  uid="$(docker run --rm --network none --memory 256m --entrypoint id "$img" -u neo4j)"
  gid="$(docker run --rm --network none --memory 256m --entrypoint id "$img" -g neo4j)"
  docker run --rm -i --network none --memory "$ADMIN_MEMORY" --memory-swap "$ADMIN_MEMORY" \
    --user "$uid:$gid" --entrypoint neo4j-admin -v "$NEO4J_VOLUME":/data "$img" "$@"
}
wait_neo4j() {
  for _ in $(seq 1 100); do
    if cypher_live 'RETURN 1' >/dev/null 2>&1; then echo "$NEO4J_C answers"; return 0; fi
    sleep 3
  done
  die "$NEO4J_C did not answer within 300 s"
}
# Stop Neo4j for an offline step; start it again on any exit, including a failure.
NEO4J_STOPPED=false
restart_neo4j() {
  local rc=$?
  if $NEO4J_STOPPED && ! running "$NEO4J_C"; then docker start "$NEO4J_C" >/dev/null; wait_neo4j || rc=1; fi
  exit "$rc"
}
stop_neo4j() {
  trap restart_neo4j EXIT
  say "stopping $NEO4J_C"
  docker stop -t 300 "$NEO4J_C" >/dev/null
  NEO4J_STOPPED=true
}
start_neo4j() {
  docker start "$NEO4J_C" >/dev/null
  wait_neo4j
  NEO4J_STOPPED=false
  trap - EXIT
}

counts() {
  local label=$1
  say "counts ($label)"
  live_mysql -N -e "SELECT 'mysql samples', COUNT(*) FROM seek_production.samples
    UNION ALL SELECT 'mysql projects', COUNT(*) FROM seek_production.projects
    UNION ALL SELECT 'mysql dmac.auth_user', COUNT(*) FROM dmac.auth_user"
  cypher_live "MATCH (s:Sample) RETURN 'neo4j Sample' AS k, count(s) AS n
UNION ALL MATCH (g:GraphMeta) RETURN 'neo4j GraphMeta ' + g.schema_version AS k, 1 AS n"
}

snapshot_complete() {
  [[ -s "$SNAP/seek_production.core.sql.gz" && -s "$SNAP/dmac.graph_search_tables.sql.gz" && -s "$SNAP/neo4j.dump" ]]
}

preflight() {
  say "preflight"
  local avail disk live_img lane_img
  avail=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
  disk=$(df -BG --output=avail /var/lib/docker 2>/dev/null | tail -1 | tr -dc '0-9' || true)
  [[ -n "$disk" ]] || disk=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  echo "MemAvailable ${avail} GiB (need ${MIN_FREE_GIB}); disk free ${disk} GB (need ${MIN_DISK_GB})"
  (( avail >= MIN_FREE_GIB )) || die "not enough free memory"
  (( disk >= MIN_DISK_GB )) || die "not enough free disk"
  for c in "$MYSQL_C" "$NEO4J_C" "$APP_C"; do running "$c" || die "container $c is not running"; done
  (cd "$MERGED" && sha256sum -c --quiet SHA256SUMS) || die "merged seeds fail their checksums"
  (cd "$GRAPH" && sha256sum -c --quiet SHA256SUMS) || die "v1.1 graph dump fails its checksum"
  live_img="$(neo4j_image)"
  lane_img="$(sed -n 's/^GS_NEO4J_IMAGE=//p' "$GS_WORK/lane.env" 2>/dev/null || true)"
  [[ -z "$lane_img" || "$lane_img" == "$live_img" ]] || die "the graph was built with $lane_img, the live Neo4j runs $live_img"
  echo "seeds verified; live Neo4j image matches the build image"
  if snapshot_complete; then echo "snapshot present: $SNAP"; else echo "no snapshot yet: run 'snapshot --yes' first"; fi
  counts "live now"
}

snapshot() {
  need_yes "$@"
  preflight
  if snapshot_complete && ! has_flag --force "$@"; then
    die "a snapshot already exists in $SNAP; it is what 'restore' puts back. Pass --force to replace it"
  fi
  umask 077; mkdir -p "$SNAP"; chmod 700 "$SNAP"
  local ignores=() t
  while read -r t; do ignores+=("--ignore-table=seek_production.$t"); done < <(
    live_mysql -N -e "SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'seek_production' AND table_name LIKE '%\\_auth\\_lookup'")
  say "snapshot seek_production (core, ${#ignores[@]} auth_lookup tables left out)"
  live_mysqldump "${ignores[@]}" seek_production | gzip -1 >"$SNAP/seek_production.core.sql.gz.partial"
  mv -f "$SNAP/seek_production.core.sql.gz.partial" "$SNAP/seek_production.core.sql.gz"
  say "snapshot dmac (${DMAC_TABLES[*]})"
  live_mysqldump dmac "${DMAC_TABLES[@]}" | gzip -1 >"$SNAP/dmac.graph_search_tables.sql.gz.partial"
  mv -f "$SNAP/dmac.graph_search_tables.sql.gz.partial" "$SNAP/dmac.graph_search_tables.sql.gz"
  counts "at snapshot" >"$SNAP/counts.txt"
  stop_neo4j
  say "snapshot Neo4j database neo4j"
  admin database dump neo4j --to-stdout >"$SNAP/neo4j.dump.partial" 2>"$SNAP/neo4j.dump.log"
  admin database load --info --from-stdin neo4j <"$SNAP/neo4j.dump.partial" >"$SNAP/neo4j.info.txt" 2>>"$SNAP/neo4j.dump.log"
  mv -f "$SNAP/neo4j.dump.partial" "$SNAP/neo4j.dump"
  start_neo4j
  (cd "$SNAP" && sha256sum seek_production.core.sql.gz dmac.graph_search_tables.sql.gz neo4j.dump >SHA256SUMS)
  chmod 600 "$SNAP"/*
  ls -l "$SNAP"
}

load() {
  need_yes "$@"
  preflight
  snapshot_complete || die "no complete snapshot in $SNAP; run 'snapshot --yes' first"
  mkdir -p "$RUNS"
  local started=$SECONDS
  say "MySQL seek_production <- merged core (about 1.9 GB of SQL; slow on a small buffer pool)"
  zcat "$MERGED/seek_production.core.sql.gz" | live_mysql seek_production
  echo "seek_production loaded in $((SECONDS - started)) s"
  say "MySQL dmac <- the merge's added rows (${DMAC_TABLES[*]})"
  local pattern
  pattern="^INSERT INTO \`($(IFS='|'; echo "${DMAC_TABLES[*]}"))\` "
  { echo 'SET NAMES utf8mb4; SET FOREIGN_KEY_CHECKS = 0;'
    zcat "$MERGED/dmac.sql.gz" | grep -E "$pattern" | sed 's/^INSERT INTO /INSERT IGNORE INTO /'
    echo 'SET FOREIGN_KEY_CHECKS = 1;'
  } | live_mysql dmac
  stop_neo4j
  say "Neo4j database neo4j <- v1.1 graph (offline load)"
  admin database load neo4j --from-stdin --overwrite-destination=true <"$GRAPH/neo4j.dump"
  start_neo4j
  counts "after load" | tee "$RUNS/counts_after_load.txt"
  echo "load took $((SECONDS - started)) s. Next: deploy feat/graph-search if it is not already, then 'verify'."
}

verify() {
  mkdir -p "$RUNS"
  counts "verify" | tee "$RUNS/counts_verify.txt"
  local n
  n=$(live_mysql -N -e "SELECT COUNT(*) FROM seek_production.samples")
  [[ "$n" == "$EXPECTED_SAMPLES" ]] || echo "WARNING: MySQL holds $n samples, not the merged $EXPECTED_SAMPLES"
  if ! docker exec "$APP_C" test -f /app/nextseek_api/management/commands/graph_sync.py; then
    die "the running app has no graph_sync command: deploy feat/graph-search, then rerun verify"
  fi
  say "graph_sync --verify against the live graph (read-only)"
  local rc=0
  docker exec -i -w /app "$APP_C" /app/.venv/bin/python manage.py graph_sync --verify --json \
    --i-mean-the-live-graph </dev/null >"$RUNS/gate_g_live.json" || rc=$?
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print('gate G live:', 'PASS' if d.get('pass') else 'FAIL');
[print('  FAIL', c['name'], 'expected', c['expected'], 'actual', c['actual']) for c in d.get('checks', []) if not c.get('pass')]" \
    "$RUNS/gate_g_live.json" || true
  if [[ -n "${GS_DEMO_PASSWORD:-}" ]]; then
    say "both endpoints answer (demo)"
    local body='{"sampletype": "TIS", "filter_searchText": "", "filter_matchType": "PARTIAL"}' ep
    for ep in advanced_search graph_search; do
      curl -s -o /dev/null -w "$ep %{http_code} %{time_total}s\n" -u "demo:$GS_DEMO_PASSWORD" \
        -H 'Content-Type: application/json' -d "$body" "http://127.0.0.1:8000/nextseek_api/samples/$ep/?page_size=1"
    done
  fi
  return "$rc"
}

restore() {
  need_yes "$@"
  snapshot_complete || die "no complete snapshot in $SNAP"
  (cd "$SNAP" && sha256sum -c --quiet SHA256SUMS) || die "the snapshot fails its checksums"
  say "MySQL seek_production <- snapshot"
  zcat "$SNAP/seek_production.core.sql.gz" | live_mysql seek_production
  say "MySQL dmac <- snapshot (${DMAC_TABLES[*]})"
  zcat "$SNAP/dmac.graph_search_tables.sql.gz" | live_mysql dmac
  stop_neo4j
  say "Neo4j database neo4j <- snapshot"
  admin database load neo4j --from-stdin --overwrite-destination=true <"$SNAP/neo4j.dump"
  start_neo4j
  counts "after restore"
  echo "restored. Rebuild from origin/dev when you are done with the branch."
}

cmd="${1:-}"; shift || true
case "$cmd" in
  preflight) preflight ;;
  snapshot) snapshot "$@" ;;
  load) load "$@" ;;
  verify) verify ;;
  restore) restore "$@" ;;
  *) echo "usage: load_live.sh preflight | snapshot --yes [--force] | load --yes | verify | restore --yes" >&2; exit 2 ;;
esac
