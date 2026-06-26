#!/usr/bin/env bash

uv run manage.py collectstatic --noinput

uv run manage.py migrate --noinput

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
