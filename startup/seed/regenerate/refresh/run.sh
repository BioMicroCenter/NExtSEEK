#!/bin/bash
# The seed refresh, one reviewed step at a time (runbook: ../README.md). Containers come from containers.sh.
#   run.sh load RAW         load RAW/{seek_production,dmac}.sql.gz and RAW/neo4j.cypher.gz, replay RAW/graph.ddl
#   run.sh before OUT       counts, schema, orphans, graph snapshot + parity, needle capture       -> OUT/before.*
#   run.sh build OUT        kill sets only (TARGETS, PROJECT_REGEX, KEEP_LOGINS); review OUT/kill_build.json
#   run.sh execute OUT      DELETE (MySQL) then DETACH DELETE (graph)                             -> OUT/kill_exec.json
#   run.sh after OUT        the same snapshots, search, and every before/after check               -> OUT/after.*
#   run.sh dump             the repo's dump_mysql.sh + dump_neo4j.py from the cleaned work containers -> startup/seed/
#   run.sh verify OUT       load startup/seed/ into the verify containers with install's code; compare to the copy
#   run.sh filestore RAW OUT   rebuild the filestore archive from RAW/filestore.raw.tar.gz        -> OUT/filestore.tar.gz
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$KIT/../../../.." && pwd)"
P=${SEED_PREFIX:-seedrefresh}
MPW=${SEED_MYSQL_PW:-${P}root}
cd "$KIT"
py() { python3 "$@"; }
gpy() { uv run --quiet --project "$REPO/startup" --group maintainer python "$@"; }
sql() { docker exec -i -e MYSQL_PWD="$MPW" "$P-mysql" mysql -uroot --default-character-set=utf8mb4 "$@"; }

case "${1:?step}" in
  load)
    RAW=${2:?RAW}
    zcat "$RAW/seek_production.sql.gz" | sql seek_production; echo "seek_production loaded"
    zcat "$RAW/dmac.sql.gz" | sql dmac; echo "dmac loaded"
    gpy graph.py load "$RAW/neo4j.cypher.gz"
    if [ -s "$RAW/graph.ddl" ]; then gpy graph.py ddl "$RAW/graph.ddl"; echo "source graph DDL replayed"; fi ;;
  before|after)
    OUT=${2:?OUT}; mkdir -p "$OUT"; step=$1
    py snap.py "$OUT/$step"
    py orphans.py "$OUT/$step.orphans.json" | head -1
    gpy graph.py snap "$OUT/$step.graph.json"
    gpy graph.py parity "$OUT/$step.parity.json" > /dev/null
    if [ "$step" = before ]; then py search.py capture; fi
    if [ "$step" = after ]; then
      py search.py scan "$OUT/after.search.json"
      echo "=== MySQL counts + schema (raw vs cleaned, same container)"; py snap.py --diff "$OUT/before" "$OUT/after"
      echo "=== orphans new since before (must be empty)"; py orphans.py --diff "$OUT/before.orphans.json" "$OUT/after.orphans.json"
      echo "=== graph"; gpy graph.py diff "$OUT/before.graph.json" "$OUT/after.graph.json"
      echo "=== parity"; py -c "import json,sys;a,b=(json.load(open(sys.argv[i])) for i in (1,2));[print(k,a.get(k),'->',b.get(k)) for k in sorted(set(a)|set(b))]" "$OUT/before.parity.json" "$OUT/after.parity.json"
    fi ;;
  build)
    OUT=${2:?OUT}; mkdir -p "$OUT"; LOG="$OUT/kill_build.json" py kill.py build ;;
  execute)
    OUT=${2:?OUT}; LOG="$OUT/kill_exec.json" py kill.py execute; gpy graph.py kill "$OUT/graph_kill.json" ;;
  dump)
    ENV="$REPO/startup/seed/regenerate/dump-source.env"
    [ -e "$ENV" ] && { echo "refusing to overwrite $ENV; move it aside first" >&2; exit 1; }
    trap 'rm -f "$ENV"' EXIT
    # throwaway credentials only; the MySQL container's own client writes the dump (no host client, no port)
    printf '%s\n' "MYSQL_HOST_DEV=127.0.0.1" "MYSQL_PORT=3306" "MYSQL_USER=root" "MYSQL_DEV_PASSWORD=$MPW" \
      "MYSQLDUMP=\"docker exec $P-mysql mysqldump\"" "NEO4J_URI=bolt://localhost:${NEO4J_PORT:-17687}" \
      "NEO4J_USER=neo4j" "NEO4J_PASSWORD=${SEED_NEO4J_PW:-${P}pass}" "NEO4J_DATABASE=neo4j" > "$ENV"
    bash "$REPO/startup/seed/regenerate/dump_mysql.sh"
    gpy "$REPO/startup/seed/regenerate/dump_neo4j.py"
    sha256sum "$REPO"/startup/seed/{seek_production,dmac}.sql.gz "$REPO"/startup/seed/neo4j.cypher.gz ;;
  verify)
    OUT=${2:?OUT}
    gpy verify.py "$REPO"
    C="$P-verify-mysql" py snap.py "$OUT/verify"
    py snap.py "$OUT/after"   # the cleaned copy, re-read now for a like-for-like compare
    py snap.py --diff "$OUT/after" "$OUT/verify"
    PORT=${VERIFY_NEO4J_PORT:-17688} gpy graph.py snap "$OUT/verify.graph.json"
    gpy graph.py diff "$OUT/after.graph.json" "$OUT/verify.graph.json"
    py final_grep.py "$OUT/final_grep.json" "$REPO"/startup/seed/{seek_production,dmac}.sql.gz "$REPO/startup/seed/neo4j.cypher.gz" ;;
  filestore)
    RAW=${2:?RAW}; OUT=${3:?OUT}
    py filestore.py "$RAW/filestore.raw.tar.gz" "$OUT/filestore.tar.gz" "$OUT/filestore.json"
    sha256sum "$OUT/filestore.tar.gz" ;;
  *) echo "unknown step $1" >&2; exit 2 ;;
esac
