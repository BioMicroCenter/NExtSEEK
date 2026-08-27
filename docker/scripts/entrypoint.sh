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

wait -n

exit $?
