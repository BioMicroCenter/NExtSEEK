#!/usr/bin/env bash
# NExtSEEK bootstrap entrypoint.
#
# Runs the typer CLI in an isolated uv environment containing only the
# bootstrap's own deps (typer, rich). --no-project avoids touching the
# main NExtSEEK [project].dependencies, which require host build tooling
# (libmysqlclient, etc.) that the bootstrap CLI itself doesn't need.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed (https://docs.astral.sh/uv/getting-started/installation/)" >&2
  exit 2
fi

exec uv run --no-project \
  --with 'typer>=0.12.0' \
  --with 'rich>=13.7.0' \
  python -m bootstrap.cli "$@"
