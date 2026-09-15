#!/usr/bin/env bash
# The Nessie evaluation venue for graph_search follow-up 1: plan docs/superpowers/plans/2026-09-15-graph-search-nessie.md
# (task T8, runbook step 1 and stage P) and spec docs/superpowers/specs/2026-09-15-graph-search-nessie-design.md,
# section 6.
#
# OPERATOR-RUN. gs-nessie-venue is a throwaway app container that runs a snapshot of this worktree's HEAD against the
# live stack's MySQL and Neo4j, for the paid Nessie runs and the read-only ground-truth oracles. It never touches a
# container of the live nextseek compose project, and nothing here prints an environment value.
#
#   GS_WORK=<work dir> NEXTSEEK_LIVE_CHECKOUT=<live checkout> scripts/graph_search/nessie_venue.sh [--dry-run] <sub>
#
#   prepare                    snapshot HEAD into $GS_WORK/nessie/venue/src, with SNAPSHOT and the rendered settings
#   up                         start the venue; refuses in the benchmark window or under GS_VENUE_MIN_GIB GiB available
#   check                      the venue check: overrides-ok and PASS; writes $GS_WORK/nessie/runs/venue_check.json
#   exec <python args>         run a Python program in the venue (the ground-truth oracles)
#   run <name> <nessie args>   a harness run in the foreground, into $GS_WORK/nessie/runs/<name>
#   bg <name> <nessie args>    the same, detached, logging to $GS_WORK/nessie/runs/<name>.console.log
#   progress <name>            questions done per arm, errors, outages, the harness state, the log's last lines
#   stop                       end the running harness; a turn in flight finishes server-side
#   logs [N]                   the venue's log: the last N lines, 200 on a terminal, all of it into a pipe
#   down                       remove the venue, then hand its files back to you
#
# --dry-run prints the docker commands of up, check, exec, run, bg, stop, logs and down instead of running them, and
# asks docker nothing. Ctrl-C on run leaves the harness going inside the venue: use stop.
#
# Two things differ from a plain `docker run --env-file`:
# - The live env files are compose env files: quoted values and $VAR references that compose resolves. docker run
#   keeps the quotes and expands nothing, so up renders them as compose does (nessie_venue_check.py env) into a
#   private file that is deleted as soon as the container exists, and sets the venue's overrides with -e.
# - The image's venv interpreter lives under /root, which no other uid can read, so the venue runs as the image's
#   root user. Every command in it runs with umask 077 and then hands what it wrote under /venue back to you; down
#   does the same for the rest, in a throwaway container.
set -euo pipefail
: "${GS_WORK:?set GS_WORK to the graph-search work directory}"
DRY=0
if [[ "${1:-}" == --dry-run ]]; then DRY=1; shift; fi
V="$GS_WORK/nessie/venue"
RUNS="$GS_WORK/nessie/runs"
NAME=gs-nessie-venue
PORT="${GS_VENUE_PORT:-8010}"
MEM="${GS_VENUE_MEMORY:-6g}"
FLOOR="${GS_VENUE_MIN_GIB:-8}"
IMAGE="${GS_VENUE_IMAGE:-nextseek-nextseek:latest}"
NETWORK="${GS_VENUE_NETWORK:-nextseek_default}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
HELPER="$HERE/nessie_venue_check.py"                    # the host's copy, next to this script
VENUE_HELPER=scripts/graph_search/nessie_venue_check.py  # the snapshot's copy, relative to /src and to the snapshot
PY=/app/.venv/bin/python
NESSIE=("$PY" manage.py nessie --tier full --user demo --password-env GS_DEMO_PASSWORD)
HARNESS="^$PY manage.py nessie"                           # the harness process only, never its wrapper shell

# Run inside the venue by root: give every root-owned path under the writable mounts to the host user named at up.
# shellcheck disable=SC2016 # expanded by the venue's shell, not here
HANDBACK='if [ -n "${GS_HOST_UID:-}" ]; then
  for d in /venue/runs /venue/truth /venue/outputs /venue/logs; do
    [ -d "$d" ] || continue
    find "$d" -xdev -uid 0 -exec chown -h "$GS_HOST_UID:$GS_HOST_GID" {} + ||
      echo "nessie_venue: could not hand $d back to uid $GS_HOST_UID" >&2
  done
fi'
# umask 077, the command, then the hand-back, keeping the command's exit code. The trap keeps a signal to the
# command from skipping the hand-back.
# shellcheck disable=SC2016
WRAP='umask 077; trap : INT TERM HUP; "$@"; rc=$?
'"$HANDBACK"'
exit "$rc"'
# shellcheck disable=SC2016
BGWRAP='umask 077; log=$1; shift; trap : INT TERM HUP; "$@" >"$log" 2>&1; rc=$?
'"$HANDBACK"'
exit "$rc"'
# shellcheck disable=SC2016
GUNICORN_WRAP='umask 077; exec "$@"'

die() { echo "nessie_venue: $*" >&2; exit 1; }
usage() {
  echo "usage: nessie_venue.sh [--dry-run] prepare | up | check | exec <python args> | run <name> <nessie args>" \
    "| bg <name> <nessie args> | progress <name> | stop | logs [N] | down" >&2
  exit 2
}

# A command as it would run; the wrappers are named, not spelled out.
show() {
  local a
  for a in "$@"; do
    if [[ "$a" == "$WRAP" ]]; then printf '%s ' "'<umask 077, run, hand files back>'"
    elif [[ "$a" == "$BGWRAP" ]]; then printf '%s ' "'<umask 077, run into the log, hand files back>'"
    elif [[ "$a" == "$HANDBACK" ]]; then printf '%s ' "'<hand root-owned files back>'"
    else printf '%q ' "$a"; fi
  done
  printf '\n'
}
run_or_show() { if (( DRY )); then show "$@"; else "$@"; fi; }

bench_check() {
  [[ ! -e "$GS_WORK/.gs-bench-running" ]] || {
    echo "nessie_venue: benchmark window open ($GS_WORK/.gs-bench-running exists): refusing" >&2; exit 3; }
}
mem_check() {
  [[ "$FLOOR" =~ ^[0-9]+$ ]] || die "GS_VENUE_MIN_GIB must be a whole number of GiB"
  local a
  a=$(awk '/^MemAvailable:/ {print int($2/1048576)}' /proc/meminfo)
  echo "MemAvailable ${a} GiB"
  (( a >= FLOOR )) || { echo "nessie_venue: refusing: under ${FLOOR} GiB available" >&2; exit 3; }
}
need_live() {
  [[ -n "${NEXTSEEK_LIVE_CHECKOUT:-}" ]] || die "set NEXTSEEK_LIVE_CHECKOUT to the checkout the live stack runs from"
  [[ -d "$NEXTSEEK_LIVE_CHECKOUT" ]] || die "NEXTSEEK_LIVE_CHECKOUT is not a directory"
}
venue_running() { [[ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" == true ]]; }
container_exists() { docker inspect "$NAME" >/dev/null 2>&1; }
need_running() { venue_running || die "the venue is not running (start it with: $0 up)"; }
harness_running() { docker exec "$NAME" pgrep -f "$HARNESS" >/dev/null 2>&1; }
check_name() {
  [[ "${1:-}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] ||
    die "a run name is 1 to 64 letters, digits, dots, dashes or underscores"
}

cmd_prepare() {
  (( $# == 0 )) || usage
  need_live
  local operator="$NEXTSEEK_LIVE_CHECKOUT/dmac/local_settings.py" new="$V/src.new" d dirty
  [[ -f "$operator" ]] || die "NEXTSEEK_LIVE_CHECKOUT has no dmac/local_settings.py"
  ! container_exists || die "$NAME exists and mounts the snapshot: run '$0 down' first"
  umask 077
  mkdir -p "$GS_WORK/nessie"
  chmod 700 "$GS_WORK/nessie"
  for d in "$V" "$V/outputs" "$V/logs" "$RUNS" "$GS_WORK/nessie/cases" "$GS_WORK/nessie/truth" \
    "$GS_WORK/nessie/questions"; do
    mkdir -p "$d"
    chmod 700 "$d"
  done
  # Build beside the current snapshot and swap at the end, so a failed prepare leaves the old one in place.
  trap 'rm -rf "$V/src.new"' EXIT
  rm -rf "$new"
  mkdir "$new"
  git -C "$REPO" archive HEAD | tar -x -C "$new"
  git -C "$REPO" rev-parse HEAD >"$new/SNAPSHOT"
  # dmac.settings creates these at import, and /src is mounted read-only.
  mkdir -p "$new/schema_rag/duckdb" "$new/schema_rag/embedding_models" "$new/dmac"
  # The snapshot's tracked template; only ASSISTANT_PARTICIPATING_PROJECTS comes from the operator's file.
  python3 "$HELPER" settings --template "$new/startup/templates/local_settings.py.template" \
    --operator "$operator" --out "$new/dmac/local_settings.py"
  rm -rf "$V/src"
  mv "$new" "$V/src"
  dirty=$(git -C "$REPO" status --porcelain --untracked-files=no | wc -l)
  (( dirty == 0 )) || echo "note: $dirty tracked files differ from HEAD; the snapshot is HEAD without them"
  echo "snapshot $(cut -c1-12 "$V/src/SNAPSHOT") ready in $V/src"
}

cmd_up() {
  (( $# == 0 )) || usage
  bench_check
  mem_check
  need_live
  local live="$NEXTSEEK_LIVE_CHECKOUT" envf="$V/.venue.env" snap head o overrides lookup=()
  [[ -s "$V/src/SNAPSHOT" && -f "$V/src/dmac/local_settings.py" ]] || die "no snapshot in $V/src: run '$0 prepare' first"
  [[ -f "$live/docker/db.env" && -f "$live/docker/nextseek.env" ]] ||
    die "NEXTSEEK_LIVE_CHECKOUT has no docker/db.env or docker/nextseek.env"
  if (( ! DRY )); then
    ! container_exists || die "$NAME already exists: run '$0 down' first"
    docker network inspect "$NETWORK" >/dev/null 2>&1 || die "no docker network $NETWORK: is the live stack up?"
  fi
  snap=$(<"$V/src/SNAPSHOT")
  head=$(git -C "$REPO" rev-parse HEAD)
  [[ "$snap" == "$head" ]] ||
    echo "note: the snapshot is ${snap:0:12} and HEAD is ${head:0:12}; run prepare to test the newer commits"
  if [[ -f "$live/.env" ]]; then lookup=(--lookup "$live/.env"); fi
  umask 077
  trap 'rm -f "$V/.venue.env"' EXIT
  python3 "$HELPER" env --out "$envf" "${lookup[@]}" "$live/docker/db.env" "$live/docker/nextseek.env"
  overrides=$(python3 "$HELPER" overrides)
  local cmd=(docker run -d --name "$NAME" --init --network "$NETWORK" -p "127.0.0.1:${PORT}:8000"
    --memory "$MEM" --memory-swap "$MEM" --env-file "$envf")
  while IFS= read -r o; do cmd+=(-e "$o"); done <<<"$overrides"
  cmd+=(-e "GS_HOST_UID=$(id -u)" -e "GS_HOST_GID=$(id -g)"
    -v "$V/src:/src:ro" -v "$V/outputs:/venue/outputs" -v "$V/logs:/venue/logs" -v "$RUNS:/venue/runs"
    -v "$GS_WORK/nessie/cases:/venue/cases:ro" -v "$GS_WORK/nessie/truth:/venue/truth"
    -w /src "$IMAGE" sh -c "$GUNICORN_WRAP" venue-gunicorn
    /app/.venv/bin/gunicorn dmac.wsgi --bind 0.0.0.0:8000 --workers 2 --threads 4 --worker-class gthread
    --timeout 1200)
  run_or_show "${cmd[@]}"
  (( DRY )) || echo "venue up on 127.0.0.1:$PORT with snapshot ${snap:0:12};" \
    "when '$0 logs | tail -5' shows the workers booted, run '$0 check'"
}

cmd_check() {
  (( $# == 0 )) || usage
  local image='<image id>' code rc=0
  if (( ! DRY )); then
    need_running
    image=$(docker inspect -f '{{.Image}}' "$NAME")
  fi
  run_or_show docker exec -i -w /src -e "GS_VENUE_IMAGE_ID=$image" "$NAME" sh -c "$WRAP" venue-check \
    "$PY" "$VENUE_HELPER" check --out /venue/runs/venue_check.json || rc=$?
  (( ! DRY )) || return 0
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:$PORT/nextseek_api/" || true)
  if [[ "$code" =~ ^[1-4][0-9][0-9]$ && "$code" != 400 ]]; then
    echo "ok    published port 127.0.0.1:$PORT answers (HTTP $code)"
  else
    echo "FAIL  published port 127.0.0.1:$PORT (HTTP ${code:-none})"
    rc=1
  fi
  return "$rc"
}

cmd_exec() {
  (( $# >= 1 )) || usage
  (( DRY )) || need_running
  run_or_show docker exec -i -w /src -e GS_DEMO_PASSWORD "$NAME" sh -c "$WRAP" venue-exec "$PY" "$@"
}

start_checks() {
  bench_check
  check_name "$1"
  [[ -n "${GS_DEMO_PASSWORD:-}" ]] || die "export GS_DEMO_PASSWORD first (plan stage P, step 0); it is never printed"
  if (( ! DRY )); then
    need_running
    ! harness_running || die "a harness run is already going in the venue: see '$0 progress <name>', or '$0 stop'"
  fi
}

cmd_run() {
  (( $# >= 1 )) || usage
  local name=$1
  shift
  start_checks "$name"
  run_or_show docker exec -i -w /src -e GS_DEMO_PASSWORD "$NAME" sh -c "$WRAP" venue-run \
    "${NESSIE[@]}" --out "/venue/runs/$name" "$@"
}

cmd_bg() {
  (( $# >= 1 )) || usage
  local name=$1
  shift
  start_checks "$name"
  run_or_show docker exec -d -w /src -e GS_DEMO_PASSWORD "$NAME" sh -c "$BGWRAP" venue-bg \
    "/venue/runs/$name.console.log" "${NESSIE[@]}" --out "/venue/runs/$name" "$@"
  (( DRY )) || echo "started $name in the background; follow it with: $0 progress $name"
}

cmd_progress() {
  (( $# == 1 )) || usage
  check_name "$1"
  local state=unknown
  if (( ! DRY )) && venue_running; then
    docker exec "$NAME" sh -c "$HANDBACK" || echo "note: could not hand the venue's files back" >&2
    if harness_running; then state=yes; else state=no; fi
  fi
  python3 "$HELPER" progress "$RUNS/$1" --log "$RUNS/$1.console.log" --running "$state"
}

cmd_stop() {
  (( $# == 0 )) || usage
  if (( DRY )); then show docker exec "$NAME" pkill -TERM -f "$HARNESS"; return 0; fi
  need_running
  if docker exec "$NAME" pkill -TERM -f "$HARNESS"; then
    echo "sent TERM to the harness; a turn in flight finishes server-side, then the run's files are handed back"
  else
    echo "no harness run in the venue"
  fi
}

cmd_logs() {
  local tail=()
  if (( $# )); then
    [[ "$1" =~ ^[0-9]+$ ]] || usage
    tail=(--tail "$1")
  elif [[ -t 1 ]]; then
    tail=(--tail 200)
  fi
  run_or_show docker logs "${tail[@]}" "$NAME" 2>&1
}

cmd_down() {
  (( $# == 0 )) || usage
  local mounts=() pair
  if (( DRY )); then
    show docker rm -f "$NAME"
  elif container_exists; then
    docker rm -f "$NAME" >/dev/null
    echo "venue removed"
  else
    echo "no venue container"
  fi
  for pair in "$V/outputs:/venue/outputs" "$V/logs:/venue/logs" "$RUNS:/venue/runs" \
    "$GS_WORK/nessie/truth:/venue/truth"; do
    [[ ! -d "${pair%:/venue/*}" ]] || mounts+=(-v "$pair")
  done
  (( ${#mounts[@]} )) || return 0
  if [[ -e "$GS_WORK/.gs-bench-running" ]]; then
    echo "benchmark window open: the venue's files were not handed back; run '$0 down' again after it"
    return 0
  fi
  run_or_show docker run --rm --name "gs-nessie-handback-$$" --network none --memory 256m --memory-swap 256m \
    -e "GS_HOST_UID=$(id -u)" -e "GS_HOST_GID=$(id -g)" "${mounts[@]}" "$IMAGE" sh -c "$HANDBACK"
  (( DRY )) || echo "the venue's files under $GS_WORK/nessie are yours again"
}

sub="${1:-}"
(( $# == 0 )) || shift
case "$sub" in
  prepare) cmd_prepare "$@" ;;
  up) cmd_up "$@" ;;
  check) cmd_check "$@" ;;
  exec) cmd_exec "$@" ;;
  run) cmd_run "$@" ;;
  bg) cmd_bg "$@" ;;
  progress) cmd_progress "$@" ;;
  stop) cmd_stop "$@" ;;
  logs) cmd_logs "$@" ;;
  down) cmd_down "$@" ;;
  *) usage ;;
esac
