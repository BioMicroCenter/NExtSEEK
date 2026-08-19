#!/usr/bin/env bash
# Run THIS checkout's Python tests inside the stack image.
#
# Why not `uv run pytest`? The repo pins mysqlclient, which does not build on a
# bare macOS host. The stack image already has every dependency, so we mount
# this checkout over /app and reuse the image's virtualenv:
#
#   -v "$HERE":/app   this checkout's code, instead of the code baked into the image
#   -v /app/.venv     anonymous volume, keeps the image's Linux venv visible
#                     through the bind mount above
#
# Compose runs from NEXTSEEK_COMPOSE_DIR because docker/*.env are gitignored and
# so are absent from a fresh worktree.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="${NEXTSEEK_COMPOSE_DIR:-$HOME/Documents/MIT/NExtSEEK}"
TARGETS="${*:-nextseek_api/tests}"

if [ ! -f "$HERE/dmac/local_settings.py" ]; then
  echo "missing $HERE/dmac/local_settings.py (gitignored)" >&2
  echo "copy it: cp $COMPOSE_DIR/dmac/local_settings.py $HERE/dmac/" >&2
  exit 1
fi

cd "$COMPOSE_DIR"
exec docker compose run --rm --no-deps \
  -v "$HERE":/app -v /app/.venv \
  nextseek bash -lc \
  "DJANGO_SETTINGS_MODULE=dmac.test_settings /app/.venv/bin/python -m pytest $TARGETS -q"
