#!/usr/bin/env bash
# NExtSEEK bootstrap entrypoint.
# Ensures uv is available, then runs the typer CLI.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed (https://docs.astral.sh/uv/getting-started/installation/)" >&2
  exit 2
fi

exec uv run python -m bootstrap.cli "$@"
