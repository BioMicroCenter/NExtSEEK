#!/usr/bin/env bash

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
