#!/usr/bin/env bash
# Dump dmac and seek_production from the configured dev MySQL into gzipped files.
# Requires dump-source.env (gitignored, maintainer-only).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEED_DIR="$SCRIPT_DIR/.."
ENV_FILE="$SCRIPT_DIR/dump-source.env"

if [[ ! -f "$ENV_FILE" ]]; then
  cat >&2 <<MSG
error: $ENV_FILE missing.
This command is maintainer-only — it requires dev DB credentials.
Copy dump-source.env.example to dump-source.env and fill in real values.
MSG
  exit 2
fi

set -a; source "$ENV_FILE"; set +a

for schema in dmac seek_production; do
  echo "dumping $schema -> $SEED_DIR/${schema}.sql.gz"
  mysqldump \
    -h "$MYSQL_HOST_DEV" -P "$MYSQL_PORT" \
    -u "$MYSQL_USER" -p"$MYSQL_DEV_PASSWORD" \
    --single-transaction --quick --routines --triggers \
    --default-character-set=utf8mb4 \
    --column-statistics=0 \
    "$schema" \
    | gzip > "$SEED_DIR/${schema}.sql.gz"
done

echo "done. Files:"
ls -lh "$SEED_DIR"/*.sql.gz
