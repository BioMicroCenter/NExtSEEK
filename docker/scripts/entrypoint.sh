#!/usr/bin/env bash

# NExtSEEK writes Excel exports + upload/dropbox staging under MEDIA_ROOT
# (/media), which is not a docker volume, so the subdirs don't exist on a fresh
# container. Without them, export/retrieve/delete/publish all 500 with
# "Cannot save file into a non-existent directory: '/media/download'".
mkdir -p /media/download /media/uploads /media/uploads/production /media/dropbox /media/reserved

# Fail-fast (R2, mirrors the FU4 migrate guard below): a silently swallowed
# collectstatic failure boots the site with missing/stale static (empty volume
# -> sitewide 404s; post-rebuild -> chat-assistant bundle 404s). Under compose
# `restart: always` this crash-loops until fixed (chosen: loud-over-up).
uv run manage.py collectstatic --noinput || {
  echo "[COLLECTSTATIC-FAILED] FATAL: manage.py collectstatic exited non-zero;" \
       "refusing to start servers with missing/stale static assets." \
       "Common cause is a full disk (ENOSPC) on the static volume." >&2
  exit 1
}

# R3: bounded DB-readiness probe. Distinguishes a transient cold-boot ordering
# gap (db container up but mysqld not yet accepting connections) from a real
# migration wedge, so the two don't share the [MIGRATE-FAILED] marker/triage.
_db_ready=0
for _i in $(seq 1 "${DB_WAIT_ATTEMPTS:-30}"); do
  if uv run manage.py shell -c "from django.db import connection; connection.ensure_connection()" >/dev/null 2>&1; then
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
uv run manage.py migrate --noinput || {
  echo "[MIGRATE-FAILED] FATAL: manage.py migrate exited non-zero;" \
       "refusing to start servers on an unmigrated schema." \
       "Fix the migration wedge (for the cc-transcript orphan guard: manual" \
       "row triage, never --fake) and let the container restart." >&2
  exit 1
}

# Web server, selectable via NEXTSEEK_SERVER (default: daphne).
#   daphne   — ASGI (dmac.asgi); serves the assistant WebSocket
#              (ws/assistant/progress/{task_id}/) live. The integration default.
#   gunicorn — WSGI (dmac.wsgi); multi-worker, no WebSocket. The chat frontend
#              auto-falls back to HTTP polling (chatApi.ts), so the assistant
#              still works; lower-risk drop-in for a shared/multi-user instance.
if [ "${NEXTSEEK_SERVER:-daphne}" = "gunicorn" ]; then
  uv run gunicorn dmac.wsgi &
else
  uv run daphne -b 0.0.0.0 -p 8000 dmac.asgi:application &
fi

uv run celery -A nextseek_api.batch_upload.celery_app worker \
              --loglevel=info \
              -Q batch_upload \
              --concurrency=1 &

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
#     container they inherit the app's, which is uncapped. `--concurrency=1`
#     still bounds the attribute worker, and a memory limit on THIS container
#     would risk the web server rather than one worker, so the ceiling is gone
#     on purpose. An attribute backlog can now cost the app container's memory.
#   * Independent restart. `wait -n` below means any one of these six processes
#     exiting takes the whole container down for compose to restart, so a
#     dispatcher crash now bounces the web server too. That is what the
#     batch_upload worker has always done here; the fold widens it to four more.
# What it did NOT give up: the queues stay separate (a backlog on one cannot
# starve the other's worker), the recovery scheduler still runs no Celery
# command and holds no broker, and the dispatcher is still the sole publisher.
#
# Two brokers in one container, on purpose. The attribute queue keeps the
# durable volume it had as a service; the batch_upload queue keeps the
# container-local default it had as a process here. Setting one container-wide
# CELERY_BROKER_URL would move batch_upload onto a volume that survives a
# recreate, and a queued upload would re-run after a deploy.
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

wait -n

exit $?
