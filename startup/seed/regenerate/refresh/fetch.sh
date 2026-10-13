#!/bin/bash
# Fetch a box's raw data, read only, over ONE ssh at a time: seek_production + dmac (mysqldump --single-transaction),
# the graph (paged read transactions), the SEEK filestore (tar). Everything streams to RAW on this machine; nothing is
# written on the box. Each stream's sha256 is computed on both ends and compared.
#
#   BOX_SSH=<ssh host> BOX_RUN_AS=<service account> BOX_REPO=<repo path on the box> [BOX_PREFIX=<instance prefix>] \
#     fetch.sh RAW [inspect|seek_production|dmac|graph|filestore ...]      (default: all, inspect first)
#
# The box account needs passwordless `sudo -n -u $BOX_RUN_AS` (or set BOX_RUN_AS empty to run as the ssh user).
# Scripts travel base64-encoded inside the ssh command, so no quoting survives to the remote shell.
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW=${1:?usage: fetch.sh RAW [parts...]}; shift
: "${BOX_SSH:?set BOX_SSH}" "${BOX_REPO:?set BOX_REPO}"
PARTS=("$@"); [ ${#PARTS[@]} -gt 0 ] || PARTS=(inspect seek_production dmac graph filestore)
mkdir -p "$RAW"

remote() {  # remote SCRIPT_TEXT [OUTFILE]
  local b64 runner="bash"
  b64=$(printf '%s' "$1" | base64 -w0)
  [ -n "${BOX_RUN_AS:-}" ] && runner="sudo -n -u $BOX_RUN_AS bash"
  if [ $# -ge 2 ]; then
    ssh -o BatchMode=yes -o ServerAliveInterval=30 "$BOX_SSH" "echo $b64 | base64 -d | $runner" > "$2"
  else
    ssh -o BatchMode=yes -o ServerAliveInterval=30 "$BOX_SSH" "echo $b64 | base64 -d | $runner"
  fi
}

check_sha() {  # FILE NAME STDERRFILE
  local box local_sha
  box=$(grep "box-sha256 $2" "$3" | awk '{print $1}')
  local_sha=$(sha256sum "$1" | awk '{print $1}')
  [ -n "$box" ] && [ "$box" = "$local_sha" ] || { echo "sha256 MISMATCH for $2: box=$box local=$local_sha" >&2; exit 1; }
  echo "$2 $(stat -c %s "$1") B sha256 $local_sha (box == local)"
}

HDR="BOX_REPO='$BOX_REPO'; BOX_PREFIX='${BOX_PREFIX:-}'"
for part in "${PARTS[@]}"; do
  case "$part" in
    inspect)
      remote "$HDR"$'\n'"$(cat "$KIT/box/inspect.sh")" | tee "$RAW/inspect.txt" ;;
    seek_production|dmac)
      remote "SCHEMA=$part; $HDR"$'\n'"$(cat "$KIT/box/stream_mysql.sh")" "$RAW/$part.sql.gz" 2> "$RAW/$part.stderr"
      check_sha "$RAW/$part.sql.gz" "$part" "$RAW/$part.stderr"
      zcat "$RAW/$part.sql.gz" | tail -c 200 | grep -q "Dump completed" || { echo "$part dump is incomplete" >&2; exit 1; } ;;
    graph)
      remote "PYB64=$(base64 -w0 "$KIT/box/graph_export.py"); $HDR"$'\n'"$(cat "$KIT/box/stream_graph.sh")" \
        "$RAW/neo4j.cypher.gz" 2> "$RAW/graph.stderr"
      check_sha "$RAW/neo4j.cypher.gz" graph "$RAW/graph.stderr"
      grep "^DDL " "$RAW/graph.stderr" > "$RAW/graph.ddl" || true
      # a live box can change during the paged read: refuse an export whose counts moved
      read -r bn br < <(sed -n 's/^before nodes=\([0-9]*\) rels=\([0-9]*\)$/\1 \2/p' "$RAW/graph.stderr")
      read -r en er an ar < <(sed -n 's/^exported nodes=\([0-9]*\) rels=\([0-9]*\); after nodes=\([0-9]*\) rels=\([0-9]*\)$/\1 \2 \3 \4/p' "$RAW/graph.stderr")
      [ -n "${bn:-}" ] && [ "$bn $br" = "$en $er" ] && [ "$bn $br" = "$an $ar" ] \
        || { echo "graph changed during the export (before $bn/$br, exported $en/$er, after $an/$ar): fetch again" >&2; exit 1; }
      echo "graph $en nodes, $er rels (unchanged during the read)" ;;
    filestore)
      remote "$HDR"$'\n'"$(cat "$KIT/box/stream_filestore.sh")" "$RAW/filestore.raw.tar.gz" 2> "$RAW/filestore.stderr"
      check_sha "$RAW/filestore.raw.tar.gz" filestore "$RAW/filestore.stderr" ;;
    *) echo "unknown part $part" >&2; exit 2 ;;
  esac
done
