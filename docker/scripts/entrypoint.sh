#!/usr/bin/env bash

# Two modes of one image (compose services `nextseek` and `web`):
#   worker (default) -- the only migrator: DB probe, migrate, the batch-upload recovery and the nine background
#                       loops. It serves nothing, so collectstatic is not its job.
#   web              -- collectstatic, DB probe, waits for the worker's migrations (it never migrates), then the
#                       web server. On a stop it drains: no new chat turns, running ones finish (capped), the rest
#                       are ended with a plain message, then the server is stopped.
MODE="${1:-worker}"
case "$MODE" in
  worker|web) ;;
  *) echo "[entrypoint] unknown mode '$MODE' (expected worker or web)" >&2; exit 2 ;;
esac

# NExtSEEK writes upload/dropbox staging and the legacy download/ tree under
# MEDIA_ROOT (/media, the nextseek-media volume shared by the worker and web),
# and a fresh volume has none of the subdirs. Without them, export/retrieve/
# delete/publish all 500 with
# "Cannot save file into a non-existent directory: '/media/download'".
mkdir -p /media/download /media/uploads /media/uploads/production /media/dropbox /media/reserved

# Fail-fast (R2, mirrors the FU4 migrate guard below): a silently swallowed
# collectstatic failure boots the site with missing/stale static (empty volume
# -> sitewide 404s; post-rebuild -> chat-assistant bundle 404s). Under compose
# `restart: always` this crash-loops until fixed (chosen: loud-over-up).
#
# Every `uv run` on the boot path passes `--no-sync`, like the loops below: the
# image's venv is final (the first-party packages are editable installs of the
# tree), and a sync re-installs those two packages on each new container's first
# command, which is time between a restart and the first served request.
# Files the app writes in shared volumes (CC user trees) stay group-writable; the agent and sidecar share uid 10001.
# gunicorn does not reset the umask in foreground workers (only its daemonize() does), so no gunicorn.conf.py change.
umask 0002

if [ "$MODE" = "web" ]; then
  # A stop before the server runs (collectstatic, the migrate wait below) exits at once: bash as PID 1 ignores a
  # TERM it has no trap for, which would sit out the whole stop_grace_period. The drain trap replaces this once the
  # server runs.
  trap 'exit 143' TERM INT
  uv run --no-sync manage.py collectstatic --noinput || {
    echo "[COLLECTSTATIC-FAILED] FATAL: manage.py collectstatic exited non-zero;" \
         "refusing to start servers with missing/stale static assets." \
         "Common cause is a full disk (ENOSPC) on the static volume." >&2
    exit 1
  }
fi

# R3: bounded DB-readiness probe. Distinguishes a transient cold-boot ordering
# gap (db container up but mysqld not yet accepting connections) from a real
# migration wedge, so the two don't share the [MIGRATE-FAILED] marker/triage.
# It opens one connection through Django's own driver (mysqlclient) with the
# values DATABASES["default"] reads, instead of starting Django to do the same:
# a Django start costs 5-10 s, on every boot, before the site can serve.
_DB_PROBE='import os, MySQLdb; MySQLdb.connect(host=os.environ["MYSQL_HOST"], port=3306, user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"], database=os.environ["NEXTSEEK_MYSQL_DATABASE"], connect_timeout=5).close()'
_db_ready=0
for _i in $(seq 1 "${DB_WAIT_ATTEMPTS:-30}"); do
  if uv run --no-sync python -c "$_DB_PROBE" >/dev/null 2>&1; then
    _db_ready=1
    break
  fi
  echo "[entrypoint] waiting for database (attempt ${_i}/${DB_WAIT_ATTEMPTS:-30})..." >&2
  sleep "${DB_WAIT_INTERVAL:-2}"
done
if [ "$_db_ready" -ne 1 ]; then
  echo "[DB-UNREACHABLE] FATAL: database not reachable before migrate." \
       "This is usually a transient whole-stack cold-boot ordering issue and" \
       "self-heals under compose restart:always. A marker PERSISTING across" \
       "many restarts indicates a real DB outage — not a migration wedge, so" \
       "do NOT run migration surgery for this." >&2
  exit 1
fi

# Fail-fast (user decision 2026-07-07): never serve on an unmigrated schema —
# a silently swallowed migrate failure is what masked the wedged 0007 for a
# week. Under compose `restart: always` this crash-loops until the wedge is
# fixed. If the failure is the heal's orphan guard, the remediation is manual
# row triage (inspect/delete/reparent orphaned assistant_cc_transcript rows);
# NEVER `migrate --fake` — that is what produced the FK-less fourth state.
if [ "$MODE" = "web" ]; then
  # Never migrates: the worker is the only migrator, and two web containers running one migration is how a
  # schema gets hurt. Wait, bounded, until it has finished; a timeout crash-loops under `restart: always`.
  _mw_deadline=$((SECONDS + ${NEXTSEEK_MIGRATE_WAIT_SECONDS:-300}))
  until uv run --no-sync manage.py migrate --check >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$_mw_deadline" ]; then
      echo "[MIGRATE-WAIT-FAILED] FATAL: migrations were still pending after" \
           "${NEXTSEEK_MIGRATE_WAIT_SECONDS:-300}s; the worker (service nextseek) applies them." \
           "Check its log for [MIGRATE-FAILED]." >&2
      exit 1
    fi
    echo "[entrypoint] waiting for the worker's migrations..." >&2
    sleep "${NEXTSEEK_MIGRATE_WAIT_INTERVAL:-5}"
  done
else
  uv run --no-sync manage.py migrate --noinput || {
    echo "[MIGRATE-FAILED] FATAL: manage.py migrate exited non-zero;" \
         "refusing to start servers on an unmigrated schema." \
         "Fix the migration wedge (for the cc-transcript orphan guard: manual" \
         "row triage, never --fake) and let the container restart." >&2
    exit 1
  }
fi

# Web server, selectable via NEXTSEEK_SERVER (default: daphne).
#   daphne   — ASGI (dmac.asgi); serves the assistant WebSocket
#              (ws/assistant/progress/{task_id}/) live. The integration default.
#   gunicorn — WSGI (dmac.wsgi); multi-worker, no WebSocket. The chat frontend
#              auto-falls back to HTTP polling (chatApi.ts), so the assistant
#              still works; lower-risk drop-in for a shared/multi-user instance.
if [ "$MODE" = "web" ]; then
  if [ "${NEXTSEEK_SERVER:-daphne}" = "gunicorn" ]; then
    uv run --no-sync gunicorn dmac.wsgi &
  else
    uv run --no-sync daphne -b 0.0.0.0 -p 8000 dmac.asgi:application &
  fi
  _server_pid=$!

  # Drain on stop. Chat turns run on threads inside the server and each leaves a marker file (turn_drain.py), so
  # the count is a directory listing. The flag makes the turn-start views answer 503; the markers of turns that
  # outlast the cap are handed to end_cut_turns, which ends them with a plain message at once.
  _turn_dir="${NEXTSEEK_TURN_DIR:-/tmp/nextseek-turns}"
  _turns_running() {
    _remaining=()
    for _m in "$_turn_dir"/*; do
      [ -f "$_m" ] && _remaining+=("$(basename "$_m")")
    done
  }
  _drain_and_stop() {
    touch "$_turn_dir/.draining"
    _d_deadline=$((SECONDS + ${NEXTSEEK_DRAIN_SECONDS:-240}))
    _turns_running
    while [ "${#_remaining[@]}" -gt 0 ] && [ "$SECONDS" -lt "$_d_deadline" ]; do
      echo "[drain] ${#_remaining[@]} turns running" >&2
      sleep "${NEXTSEEK_DRAIN_POLL:-10}"
      _turns_running
    done
    if [ "${#_remaining[@]}" -gt 0 ]; then
      echo "[drain] cap reached; ending ${#_remaining[@]} turns" >&2
      uv run --no-sync manage.py end_cut_turns "${_remaining[@]}" || echo "[drain] end_cut_turns failed" >&2
    fi
    kill -TERM "$_server_pid" 2>/dev/null
    wait "$_server_pid"
    exit 143
  }
  rm -rf "$_turn_dir"   # a restarted container keeps /tmp: no stale flag or marker may survive the start
  mkdir -p "$_turn_dir"
  trap _drain_and_stop TERM INT
  wait "$_server_pid"
  exit $?
fi

case "${BATCH_UPLOAD_STATE_DIR:-/var/celery}" in
  /var/celery|/var/celery/*) ;;
  *) echo "[entrypoint] WARNING: BATCH_UPLOAD_STATE_DIR=${BATCH_UPLOAD_STATE_DIR} is not under /var/celery (the batch_upload_state volume): queued uploads will not survive a recreate" >&2 ;;
esac

# Boot recovery: no worker is running yet, so any job still STARTED/PROGRESS was killed by the restart.
# Fail it (never re-run it) so its status page ends. Queued, not-yet-started jobs are kept.
uv run --no-sync manage.py mark_interrupted_batch_jobs || echo "[entrypoint] mark_interrupted_batch_jobs failed; continuing" >&2

uv run --no-sync celery -A nextseek_api.batch_upload.celery_app worker \
              --loglevel=info \
              -Q batch_upload \
              --concurrency=1 &

# The Container-CC summary sweep (paid, ~200 s on a backlog) gets its own worker on the same broker, so it never
# holds up an upload. Same shape as the worker plus beat sharing that broker file.
uv run --no-sync celery -A nextseek_api.batch_upload.celery_app worker \
              --loglevel=info \
              -Q cc_summaries \
              --hostname=cc_summaries@%h \
              --concurrency=1 &

# The beat that queues the periodic jobs in celery_app.py (summary sweep, stuck-turn reaper, session clean-up).
# Its schedule file sits on /var/celery; every entry expires, so a busy worker drops missed runs.
uv run --no-sync celery -A nextseek_api.batch_upload.celery_app beat \
              --loglevel=info \
              --schedule "${BATCH_UPLOAD_STATE_DIR:-/var/celery}/celerybeat-schedule" &

# ---------------------------------------------------------------------------
# The attribute-mutation pipeline and the assay-registration drain loop.
#
# These were four compose services (attribute_mutation_worker, _dispatcher,
# _recovery_scheduler, assay_registration_worker) behind two `profiles:` keys
# until 2026-09-02. Nothing persisted COMPOSE_PROFILES, so `./startup.sh
# rebuild` moved the app to the new image and left them on the old one under
# `restart: unless-stopped` -- old code, reported healthy. They run here now,
# the way the batch_upload worker above always has, so there is nothing left
# behind to go stale. The arguments below are the retired services' commands
# carried forward verbatim, `--no-sync` included: it skips uv's environment
# resolution, which four concurrent `uv run` invocations do not need and would
# otherwise contend on.
#
# WHAT THE FOLD GAVE UP, deliberately (Taisha's DD-29, task-08):
#   * Container-level isolation. Each of the four carried its own CPU and
#     memory ceiling (1.0/1G for the worker, 0.25/1G for the rest); inside this
#     container they share the worker's (NEXTSEEK_WORKER_MEMORY since the web
#     server moved to its own `web` service). `--concurrency=1` still bounds the
#     attribute worker. An attribute backlog can cost the other loops' memory,
#     never the web server's.
#   * Independent restart. `wait -n` below means any one of these background processes
#     exiting takes the whole worker container down for compose to restart, so a
#     dispatcher crash bounces the other loops too (not the web server, which runs
#     in `web`). That is what the batch_upload worker has always done here; the
#     fold widens it to four more.
# What it did NOT give up: the queues stay separate (a backlog on one cannot
# starve the other's worker), the recovery scheduler still runs no Celery
# command and holds no broker, and the dispatcher is still the sole publisher.
#
# Two brokers in one container, on purpose. The attribute queue keeps the
# durable volume it had as a service; the batch_upload queue keeps its own
# default, SQLite files under BATCH_UPLOAD_STATE_DIR (/var/celery, the
# batch_upload_state volume), so a queued upload survives a recreate. Setting one
# container-wide CELERY_BROKER_URL would put the two queues on one broker.
_ATTRIBUTE_BROKER="sqla+sqlite:////var/lib/attribute-broker/broker.sqlite3"

CELERY_BROKER_URL="$_ATTRIBUTE_BROKER" \
  uv run --no-sync celery -A nextseek_api.batch_upload.celery_app worker \
                   --loglevel=info \
                   -Q attribute_mutations \
                   --hostname=attribute_mutations@%h \
                   --concurrency="${ATTRIBUTE_MUTATION_WORKER_CONCURRENCY:-1}" &

# The transactional-outbox dispatcher: the sole publisher for
# `attribute_mutations` messages. Same broker as the worker above, or it
# publishes where nothing is listening.
CELERY_BROKER_URL="$_ATTRIBUTE_BROKER" \
  uv run --no-sync python manage.py dispatch_attribute_outbox &

# Idempotent sync-job recovery. No broker and no Celery command, so it can
# never consume either queue -- only scan/claim/reconcile via the default
# database. Keep it that way.
uv run --no-sync python manage.py recover_attribute_sync_jobs \
                 --loop --interval-seconds 30 &

# Drains the batch assay-registration queue by MySQL lease. No Celery, no
# queue. POST /nextseek_api/assay-registrations/ answers 202 with a status_url
# for any batch above the row threshold, and NOTHING claims those jobs unless
# this runs -- the URL then reports `accepted`, 0 of N, forever.
uv run --no-sync python manage.py run_assay_registration_jobs --interval 5 &

# Drains the sample-share queue: plans a share's dry run, then runs its link unit once the admin's apply
# calls have made its clones. POST /nextseek_api/sample-shares/ answers 202, and nothing plans it unless this runs.
uv run --no-sync python manage.py run_share_jobs --interval 5 &

# --- graph sync loop (BEGIN) -----------------------------------------------
# Graph 2.0's own sync: the schedule (nightly reconcile, nightly drift, weekly
# full sync), the outbox drain, and the heavy runs as its own child processes.
# On by default; NEXTSEEK_GRAPH_SYNC_LOOP=0 is the off switch for a box whose
# graph is not ready for it. The loop writes nothing to a graph that is not at
# the writer's schema version, so it is safe here before a box's first full
# sync: it reports pending and waits.
#
# An `if` block around a backgrounded restart loop, and never a backgrounded
# test (`[ ... ] && ( ... ) &`, which creates the job whatever the switch
# says): `wait -n` below returns as soon as ANY background job exits and takes
# the whole container down, so the loop gets a `while` that never returns, and
# with the switch off there is no job for `wait -n` to see at all. This is the
# one runtime of this container that has to survive its own crash instead of
# bouncing every other loop with it, which is why it restarts itself after a
# delay rather than leaving that to compose. The contract is executed, block
# and all, by nextseek_api/tests/test_graph_sync_entrypoint.py.
if [ "${NEXTSEEK_GRAPH_SYNC_LOOP:-1}" = "1" ]; then
  ( while :; do uv run --no-sync python manage.py graph_sync --loop; sleep "${GRAPH_SYNC_RESTART_DELAY:-60}"; done ) &
fi
# --- graph sync loop (END) -------------------------------------------------

# `docker stop` sends SIGTERM to PID 1, which is this script. Without a trap,
# bash as PID 1 ignores it, so every stop waited out docker's 10 s and ended in
# SIGKILL with gunicorn and the workers never told. Hand it on and wait for them.
# The graph-sync loop is the exception: its subshell dies on the signal and the
# sync it was running is killed with the container, as before (the loop has no
# shutdown handler; an interrupted run is picked up by the next schedule).
trap 'kill -TERM $(jobs -p) 2>/dev/null; wait; exit 143' TERM INT

wait -n

exit $?
