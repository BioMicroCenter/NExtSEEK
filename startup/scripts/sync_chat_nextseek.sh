#!/usr/bin/env bash
# Snapshot-sync chat_nextseek from its canonical repo into NExtSEEK.
#
# chat_nextseek lives at github.com:cdemurjian/chat_nextseek.git as its own
# independent private repo. NExtSEEK ships a *vendored snapshot* of it under
# chat_nextseek/ so end-users only need to clone NExtSEEK to install. This
# script is the maintainer's tool for bumping that snapshot to a new commit.
#
# Usage: sync_chat_nextseek.sh <path/to/canonical/chat_nextseek>

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <path/to/canonical/chat_nextseek>" >&2
  exit 2
fi

SOURCE="$(cd "$1" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEST="$REPO_ROOT/chat_nextseek"

if [[ ! -d "$SOURCE/.git" ]]; then
  echo "error: $SOURCE doesn't look like a git repo (no .git/ found)" >&2
  exit 2
fi

if ! git -C "$SOURCE" diff-index --quiet HEAD; then
  echo "error: source has uncommitted changes — commit or stash first" >&2
  exit 2
fi

SOURCE_SHA=$(git -C "$SOURCE" rev-parse HEAD)
echo "syncing $SOURCE @ $SOURCE_SHA -> $DEST"

rsync -a --delete \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='outputs/' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  --exclude='*.egg-info/' \
  --exclude='.mcp.json' \
  --exclude='*.sqlite' \
  "$SOURCE/" "$DEST/"

echo "$SOURCE_SHA" > "$DEST/.chat_nextseek_snapshot"

cat <<MSG

done.

review with:
  git -C $REPO_ROOT status chat_nextseek/

commit with:
  git -C $REPO_ROOT add chat_nextseek/
  git -C $REPO_ROOT commit -m "sync chat_nextseek to ${SOURCE_SHA:0:8}"
MSG
