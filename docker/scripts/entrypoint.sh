#!/usr/bin/env bash

uv run manage.py collectstatic --noinput

uv run manage.py migrate --noinput

uv run gunicorn dmac.wsgi &

uv run celery -A nextseek_api.batch_upload.celery_app worker \
              --loglevel=info \
              -Q batch_upload \
              --concurrency=1 &

wait -n

exit $?
