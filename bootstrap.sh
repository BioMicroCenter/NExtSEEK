#!/usr/bin/env bash
# NExtSEEK bootstrap entrypoint.
#
# Runs the typer CLI using bootstrap/pyproject.toml as its own uv project,
# fully isolated from the main NExtSEEK [project].dependencies (which require
# host build tooling like libmysqlclient that the bootstrap CLI doesn't need).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed (https://docs.astral.sh/uv/getting-started/installation/)" >&2
  exit 2
fi

exec uv run --project bootstrap python -m bootstrap "$@"
