#!/usr/bin/env bash

# NExtSEEK writes Excel exports + upload/dropbox staging under MEDIA_ROOT
# (/media), which is not a docker volume, so the subdirs don't exist on a fresh
# container. Without them, export/retrieve/delete/publish all 500 with
# "Cannot save file into a non-existent directory: '/media/download'".
mkdir -p /media/download /media/uploads /media/uploads/production /media/dropbox /media/reserved

uv run manage.py collectstatic --noinput

uv run manage.py migrate --noinput

uv run gunicorn dmac.wsgi &

uv run celery -A nextseek_api.batch_upload.celery_app worker \
              --loglevel=info \
              -Q batch_upload \
              --concurrency=1 &

wait -n

exit $?
