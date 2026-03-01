#!/usr/bin/env bash
# post_uv_sync.sh — Run after `uv sync` to restore symlinks broken by venv rebuild.
set -euo pipefail

VENV_DIR="${1:-/opt/NExtSEEK/.venv}"
DATA_DIR="/opt/NExtSEEK/data"
CHAT_PKG="$VENV_DIR/lib/python3.14/site-packages/chat_nextseek"
TEMPLATE_SRC="$DATA_DIR/seq_template.xlsx"
TEMPLATE_DST="$CHAT_PKG/reports/seq_template.xlsx"

# Download template if not cached locally
if [ ! -f "$TEMPLATE_SRC" ]; then
    echo "Downloading GEO seq_template.xlsx from NCBI..."
    mkdir -p "$DATA_DIR"
    curl -sSL -o "$TEMPLATE_SRC" "https://www.ncbi.nlm.nih.gov/geo/info/examples/seq_template.xlsx"
fi

# Recreate symlink
mkdir -p "$(dirname "$TEMPLATE_DST")"
ln -sf "$TEMPLATE_SRC" "$TEMPLATE_DST"
echo "✓ Symlinked seq_template.xlsx → $TEMPLATE_DST"
