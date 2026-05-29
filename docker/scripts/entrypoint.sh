#!/usr/bin/env bash

uv run manage.py collectstatic --noinput

uv run manage.py migrate --noinput

# ASGI server (daphne) instead of gunicorn/WSGI so the assistant websocket
# (ws/assistant/progress/{task_id}/) is actually served — dmac/asgi.py already
# wires http + websocket via Channels. This is what makes "maintain websockets"
# true; gunicorn dmac.wsgi could not serve the WS path (frontend fell back to
# polling). daphne is already a project dependency.
uv run daphne -b 0.0.0.0 -p 8000 dmac.asgi:application &

uv run celery -A nextseek_api.batch_upload.celery_app worker \
              --loglevel=info \
              -Q batch_upload \
              --concurrency=1 &

wait -n

exit $?
