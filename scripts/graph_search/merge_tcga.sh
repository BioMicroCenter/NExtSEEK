#!/usr/bin/env bash
# Builds the merged MySQL for graph_search (plan task M1, gate M) inside the lane's scratch MySQL:
# the local production backup as the base, plus TCGA from the dev-box dump in a new project.
#
#   merge_tcga.sh [all]   load, verify (expected to fail on TCGA), merge, verify, dump
#   merge_tcga.sh load    move the dev data to dev_seek/dev_dmac, load the local backup as
#                         seek_production/dmac, snapshot the pre-merge checksums
#   merge_tcga.sh merge   run merge_tcga.sql (needs a fresh load)
#   merge_tcga.sh verify [name]  post-merge checksums, verify_merge.sql, $GS_WORK/runs/M1/<name>.json
#                         (default gate_m); exits 1 when a check fails
#   merge_tcga.sh dump    dump the merged schemas to $GS_WORK/seeds/merged-2026-09-14/
#
# Every phase is re-runnable: `load` always starts again from the backup, and it never drops
# seek_production or dmac unless the dev data is already safe in dev_seek and dev_dmac.
# Secrets come from $GS_WORK/lane.env and reach the container as a bare `-e MYSQL_PWD`.
set -euo pipefail
: "${GS_WORK:?set GS_WORK to the graph-search work directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LANE="$HERE/lane.sh"
# shellcheck disable=SC1091
source "$GS_WORK/lane.env"
MYSQL_C="${GS_MYSQL_CONTAINER:-gs-scratch-devbox-mysql}"
LOCAL_SEEDS="$GS_WORK/seeds/local-2026-09-14"
MERGED_SEEDS="$GS_WORK/seeds/merged-2026-09-14"
RUN_DIR="$GS_WORK/runs/M1"
DEV_SAMPLES=984672

# Tables the merge appends to. For these the pre-existing rows are copied and checksummed; every
# other table is checksummed whole. Format: schema.table:column[:reference schema.table[:next]].
# The pre-existing rows are those with column <= the snapshot's MAX(id) of the reference table
# (default the table itself), or, with `next`, every row whose column is not the reference table's
# next id (projects_sample_types holds orphan rows above the local maximum project id).
# max_ref in the snapshot records that bound: the maximum, or with `next` the excluded id.
APPENDED=(
  seek_production.projects:id
  seek_production.work_groups:id
  seek_production.group_memberships:id
  seek_production.people:id
  seek_production.users:id
  seek_production.policies:id
  seek_production.permissions:id
  seek_production.investigations:id
  seek_production.investigations_projects:investigation_id:seek_production.investigations
  seek_production.studies:id
  seek_production.assays:id
  seek_production.sample_types:id
  seek_production.projects_sample_types:project_id:seek_production.projects:next
  seek_production.sample_attributes:id
  seek_production.samples:id
  seek_production.projects_samples:sample_id:seek_production.samples
  seek_production.assay_assets:id
  dmac.internal_assays:id
  dmac.assays_internal_assays:id
  dmac.sample_types_clades:id
  dmac.auth_user:id
  dmac.seek_user_profile:id
)

log() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
die() { log "FAILED: $*"; exit 1; }
# sql '<statements>' runs SQL and prints bare tab-separated rows; sql - reads stdin.
sql() { "$LANE" mysql "${1:--}" -N --batch "${@:2}"; }
table_count() { # schema -> number of base tables
  sql "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$1' AND table_type='BASE TABLE'"
}
samples_in() { # schema -> samples row count, or -1 when there is no samples table
  if [[ "$(sql "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$1' AND table_name='samples'")" == 1 ]]
  then sql "SELECT COUNT(*) FROM \`$1\`.samples"; else echo -1; fi
}

move_dev_aside() {
  if (( $(table_count dev_seek) > 0 )); then
    [[ "$(samples_in dev_seek)" == "$DEV_SAMPLES" ]] || die "dev_seek exists but does not hold the dev data"
    (( $(table_count dev_dmac) > 0 )) || die "dev_seek holds the dev data but dev_dmac is empty"
    log "dev data already in dev_seek and dev_dmac"
    return
  fi
  [[ "$(samples_in seek_production)" == "$DEV_SAMPLES" ]] \
    || die "seek_production does not hold the dev dump ($DEV_SAMPLES samples); refusing to move it"
  (( $(table_count dev_dmac) == 0 )) || die "dev_dmac already has tables"
  log "moving the dev data to dev_seek and dev_dmac (one atomic RENAME)"
  sql "CREATE DATABASE IF NOT EXISTS dev_seek CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
       CREATE DATABASE IF NOT EXISTS dev_dmac CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
  local renames
  renames=$(sql "SET SESSION group_concat_max_len=1000000;
                 SELECT GROUP_CONCAT(CONCAT('\`',table_schema,'\`.\`',table_name,'\` TO \`',
                   IF(table_schema='seek_production','dev_seek','dev_dmac'),'\`.\`',table_name,'\`')
                   SEPARATOR ', ')
                 FROM information_schema.tables
                 WHERE table_schema IN ('seek_production','dmac') AND table_type='BASE TABLE'")
  sql "RENAME TABLE $renames"
  [[ "$(samples_in dev_seek)" == "$DEV_SAMPLES" ]] || die "dev_seek.samples count after the move"
  log "dev_seek: $(table_count dev_seek) tables, dev_dmac: $(table_count dev_dmac) tables"
}

load_local() {
  (cd "$LOCAL_SEEDS" && sha256sum -c --quiet SHA256SUMS) || die "local backup checksums"
  log "recreating seek_production and dmac"
  sql "DROP DATABASE IF EXISTS seek_production;
       CREATE DATABASE seek_production CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
       DROP DATABASE IF EXISTS dmac;
       CREATE DATABASE dmac CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
  log "loading the local seek_production core"
  zcat "$LOCAL_SEEDS/seek_production.core.sql.gz" | "$LANE" mysql - seek_production
  log "loading the local dmac"
  zcat "$LOCAL_SEEDS/dmac.sql.gz" | "$LANE" mysql - dmac
  local n; n=$(samples_in seek_production)
  log "local load done: seek_production $(table_count seek_production) tables, $n samples; dmac $(table_count dmac) tables"
}

# checksums <target table in dmac>: CHECKSUM TABLE for every base table of seek_production and
# dmac (gs_* excluded). An appended table gets the checksum of a copy of its pre-existing rows;
# the filter comes from the snapshot when one exists, so pre and post use the same rows.
checksums() {
  local target="$1" tmp; tmp=$(mktemp)
  sql "DROP TABLE IF EXISTS dmac.$target;
       CREATE TABLE dmac.$target (
         schema_name VARCHAR(64) NOT NULL, table_name VARCHAR(64) NOT NULL,
         scope VARCHAR(8) NOT NULL, row_filter VARCHAR(255) NULL, max_ref BIGINT NULL,
         row_count BIGINT NULL, checksum BIGINT UNSIGNED NULL,
         PRIMARY KEY (schema_name, table_name)) CHARACTER SET ascii;"
  local tables
  if [[ "$target" == gs_premerge_checksums ]]; then
    tables=$(sql "SELECT CONCAT(table_schema,'.',table_name) FROM information_schema.tables
                  WHERE table_schema IN ('seek_production','dmac') AND table_type='BASE TABLE'
                    AND table_name NOT LIKE 'gs\\_%' ORDER BY 1")
  else
    tables=$(sql "SELECT CONCAT(schema_name,'.',table_name) FROM dmac.gs_premerge_checksums ORDER BY 1")
  fi
  local -A appended=()
  local spec name col ref mode
  for spec in "${APPENDED[@]}"; do
    IFS=: read -r name col ref mode <<<"$spec"; appended[$name]="$col:${ref:-$name}:${mode:-}"
  done
  local whole=() t
  for t in $tables; do [[ -n "${appended[$t]:-}" ]] || whole+=("$t"); done
  local -A present=()
  for t in $(sql "SELECT CONCAT(table_schema,'.',table_name) FROM information_schema.tables
                  WHERE table_schema IN ('seek_production','dmac') AND table_type='BASE TABLE'"); do
    present[$t]=1
  done
  # Whole tables: CHECKSUM TABLE in chunks, and every row count in one UNION ALL (a table that is
  # gone keeps NULL for both, which fails verification).
  {
    local chunk=() counts=() i
    for ((i = 0; i < ${#whole[@]}; i++)); do
      chunk+=("${whole[$i]}")
      if (( ${#chunk[@]} == 40 || i == ${#whole[@]} - 1 )); then
        sql "CHECKSUM TABLE $(IFS=,; echo "${chunk[*]}")" 2>/dev/null | awk -F'\t' '{print "sum\t" $1 "\t" $2}'
        chunk=()
      fi
    done
    for t in "${whole[@]}"; do
      [[ -n "${present[$t]:-}" ]] && counts+=("SELECT 'cnt', '$t', COUNT(*) FROM $t")
    done
    local q="${counts[0]}"
    for ((i = 1; i < ${#counts[@]}; i++)); do q+=" UNION ALL ${counts[$i]}"; done
    sql "$q"
  } >"$tmp"
  {
    echo "INSERT INTO dmac.$target (schema_name, table_name, scope) VALUES"
    local first=1
    for t in "${whole[@]}"; do
      (( first )) || echo ","; first=0
      printf "('%s','%s','table')" "${t%%.*}" "${t#*.}"
    done
    echo ";"
    awk -F'\t' -v tgt="$target" '
      $1=="sum" && $3!="NULL" && $3!="" { split($2,a,"."); printf "UPDATE dmac.%s SET checksum=%s WHERE schema_name=\x27%s\x27 AND table_name=\x27%s\x27;\n", tgt, $3, a[1], a[2] }
      $1=="cnt" { split($2,a,"."); printf "UPDATE dmac.%s SET row_count=%s WHERE schema_name=\x27%s\x27 AND table_name=\x27%s\x27;\n", tgt, $3, a[1], a[2] }' "$tmp"
  } | sql -
  # Appended tables: checksum a copy of the pre-existing rows.
  local filter cols sum cnt max
  for t in $tables; do
    [[ -n "${appended[$t]:-}" ]] || continue
    IFS=: read -r col ref mode <<<"${appended[$t]}"
    if [[ "$target" == gs_premerge_checksums && "$mode" == next ]]; then
      # The id merge_tcga.sql will give the new row: GREATEST(MAX(id) + 1, live AUTO_INCREMENT).
      max=$(sql "SET SESSION information_schema_stats_expiry = 0;
                 SELECT GREATEST(COALESCE(MAX(id),0) + 1,
                   (SELECT COALESCE(AUTO_INCREMENT,0) FROM information_schema.tables
                    WHERE table_schema='${ref%%.*}' AND table_name='${ref#*.}')) FROM $ref")
      filter="($col <> $max OR $col IS NULL)"
    elif [[ "$target" == gs_premerge_checksums ]]; then
      max=$(sql "SELECT COALESCE(MAX(id),0) FROM $ref")
      filter="($col <= $max OR $col IS NULL)"
    else
      read -r max filter < <(sql "SELECT max_ref, row_filter FROM dmac.gs_premerge_checksums
                                  WHERE CONCAT(schema_name,'.',table_name)='$t'")
    fi
    cols=$(sql "SELECT GROUP_CONCAT(CONCAT('\`',column_name,'\`') ORDER BY ordinal_position)
                FROM information_schema.columns
                WHERE CONCAT(table_schema,'.',table_name)='$t' AND extra NOT LIKE '%GENERATED%'")
    read -r sum cnt < <(sql "DROP TABLE IF EXISTS dmac.gs_cmp;
      CREATE TABLE dmac.gs_cmp LIKE $t;
      INSERT INTO dmac.gs_cmp ($cols) SELECT $cols FROM $t WHERE $filter;
      CHECKSUM TABLE dmac.gs_cmp;
      SELECT COUNT(*) FROM dmac.gs_cmp;
      DROP TABLE dmac.gs_cmp;" | awk -F'\t' 'NR==1{s=$2} NR==2{c=$1} END{print s, c}')
    if [[ "$target" == gs_premerge_checksums ]]; then
      local total; total=$(sql "SELECT COUNT(*) FROM $t")
      [[ "$total" == "$cnt" ]] || die "$t: the filter $filter selects $cnt of $total rows"
    fi
    sql "INSERT INTO dmac.$target (schema_name, table_name, scope, row_filter, max_ref, row_count, checksum)
         VALUES ('${t%%.*}','${t#*.}','rows','$filter',$max,$cnt,$sum)"
  done
  log "checksums in dmac.$target: $(sql "SELECT COUNT(*) FROM dmac.$target") tables"
}

phase_load() {
  "$LANE" free-check
  move_dev_aside
  load_local
  checksums gs_premerge_checksums
}

phase_merge() {
  "$LANE" free-check
  log "running merge_tcga.sql"
  "$LANE" mysql - <"$HERE/merge_tcga.sql"
  log "merge done"
}

# verify [report name]: exits 1 when any check fails.
phase_verify() {
  local report="${1:-gate_m}"
  mkdir -p "$RUN_DIR"
  checksums gs_postmerge_checksums
  log "running verify_merge.sql"
  "$LANE" mysql - -N --batch <"$HERE/verify_merge.sql" >"$RUN_DIR/$report.tsv"
  python3 - "$RUN_DIR/$report.tsv" "$RUN_DIR/$report.json" <<'PY'
import csv, json, sys
from datetime import datetime, timezone
src, dest = sys.argv[1], sys.argv[2]
checks = []
with open(src, newline="") as fh:
    for row in csv.reader(fh, delimiter="\t"):
        if not row or row[0] == "check_name":
            continue
        name, expected, actual, passed = row[:4]
        checks.append({"check": name, "expected": expected, "actual": actual, "pass": passed == "1"})
report = {
    "gate": "M",
    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "pass": bool(checks) and all(c["pass"] for c in checks),
    "checks": checks,
}
with open(dest, "w") as fh:
    json.dump(report, fh, indent=2)
failed = [c["check"] for c in checks if not c["pass"]]
print(f"{len(checks)} checks, {len(failed)} failed" + (": " + ", ".join(failed) if failed else ""))
sys.exit(0 if report["pass"] else 1)
PY
}

phase_dump() {
  mkdir -p "$MERGED_SEEDS"; chmod 700 "$MERGED_SEEDS"
  local schema dest args
  for schema in seek_production dmac; do
    if [[ "$schema" == seek_production ]]; then dest="$MERGED_SEEDS/seek_production.core.sql.gz"; args=(seek_production)
    else dest="$MERGED_SEEDS/dmac.sql.gz"
      args=(--ignore-table=dmac.gs_premerge_checksums --ignore-table=dmac.gs_postmerge_checksums dmac); fi
    log "dumping $schema"
    ( umask 077
      MYSQL_PWD="$GS_MYSQL_ROOT_PASSWORD" docker exec -i -e MYSQL_PWD "$MYSQL_C" mysqldump -uroot \
        --single-transaction --quick --routines --triggers --default-character-set=utf8mb4 \
        --hex-blob --set-gtid-purged=OFF "${args[@]}" | gzip -6 >"$dest.partial"
      rc=("${PIPESTATUS[@]}"); echo "${rc[*]}" >"$dest.rc"
      [[ "${rc[*]}" == "0 0" ]] || exit 1 ) || die "mysqldump of $schema"
    gzip -t "$dest.partial" || die "gzip -t $dest.partial"
    zcat "$dest.partial" | tail -1 | grep -q '^-- Dump completed' || die "$schema dump is truncated"
    mv "$dest.partial" "$dest"; chmod 600 "$dest" "$dest.rc"
  done
  write_manifest
}

write_manifest() {
  local m="$MERGED_SEEDS/MANIFEST.md"
  ( cd "$MERGED_SEEDS" && sha256sum seek_production.core.sql.gz dmac.sql.gz >SHA256SUMS && chmod 600 SHA256SUMS )
  python3 - "$MERGED_SEEDS" "$RUN_DIR/gate_m.json" >"$m" <<'PY'
import json, os, subprocess, sys
seeds, gate = sys.argv[1], sys.argv[2]
report = json.load(open(gate))
print("# Merged MySQL for graph_search, 2026-09-14 (local production plus TCGA)\n")
print("REAL personal data: never copy these files into a repository, never upload them.\n")
print("Built by `scripts/graph_search/merge_tcga.sh` (plan task M1) in the lane's scratch MySQL. The dumps")
print("use the plain database form (no CREATE DATABASE or USE), so a restore names the target schema.\n")
print("| File | Bytes (gz) | Bytes (raw) | sha256 |")
print("|---|---:|---:|---|")
for name in ("seek_production.core.sql.gz", "dmac.sql.gz"):
    path = os.path.join(seeds, name)
    raw = int(subprocess.run(f"zcat '{path}' | wc -c", shell=True, check=True,
                             capture_output=True, text=True).stdout)
    sha = subprocess.run(["sha256sum", path], check=True, capture_output=True, text=True).stdout.split()[0]
    print(f"| `{name}` | {os.path.getsize(path):,} | {raw:,} | `{sha}` |")
print()
print("`seek_production.core.sql.gz` holds every table of the merged `seek_production` (the local backup's")
print("core, which has no `*_auth_lookup` tables). `dmac.sql.gz` holds the merged `dmac`, including the")
print("remap table `gs_remap(kind, old_id, new_id)`; the checksum snapshots are left out.\n")
print(f"## Gate M: {'PASS' if report['pass'] else 'FAIL'} ({report['generated_at']})\n")
print("| Check | Expected | Actual | Pass |")
print("|---|---|---|---|")
for c in report["checks"]:
    print(f"| {c['check']} | {c['expected']} | {c['actual']} | {'yes' if c['pass'] else 'NO'} |")
PY
  chmod 600 "$m"
  log "wrote $m"
}

case "${1:-all}" in
  load) phase_load ;;
  merge) phase_merge ;;
  verify) phase_verify "${2:-gate_m}" ;;
  dump) phase_dump ;;
  all)
    phase_load
    phase_verify gate_m.premerge || log "pre-merge verify failed, as expected before the merge"
    phase_merge
    phase_verify gate_m
    phase_dump ;;
  *) echo "usage: merge_tcga.sh [all|load|merge|verify|dump]" >&2; exit 2 ;;
esac
