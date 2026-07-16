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

BASE_ARGS=(
  -h "$MYSQL_HOST_DEV" -P "$MYSQL_PORT"
  -u "$MYSQL_USER" -p"$MYSQL_DEV_PASSWORD"
  --single-transaction --quick
  --default-character-set=utf8mb4
  --column-statistics=0
)

for schema in dmac seek_production; do
  echo "dumping $schema -> $SEED_DIR/${schema}.sql.gz"
  if [[ "$schema" == "seek_production" ]]; then
    # SEEK's `settings` table holds its DB-backed config, and `site_base_host` in
    # particular is PER-INSTANCE deployment config, not seed data: dev, prod and a
    # laptop each need a different hostname. This seed is universal, so baking one
    # instance's hostname into it would silently repoint every identifier a fresh
    # install publishes (SEEK IDs, JSON-LD @id, sitemap) at the wrong host — the
    # source DB legitimately has such a row, so an unfiltered dump WOULD capture it.
    # Dump everything except `settings`, then re-dump `settings` with that row
    # filtered out. Startup applies the correct per-instance value after seeding
    # (startup/steps/seek_settings.py); startup/tests/test_seed.py locks this.
    {
      mysqldump "${BASE_ARGS[@]}" --routines --triggers \
        --ignore-table="${schema}.settings" "$schema"
      mysqldump "${BASE_ARGS[@]}" \
        --where="var <> 'site_base_host'" "$schema" settings
    } | gzip > "$SEED_DIR/${schema}.sql.gz"
  else
    mysqldump "${BASE_ARGS[@]}" --routines --triggers "$schema" \
      | gzip > "$SEED_DIR/${schema}.sql.gz"
  fi
done

echo "done. Files:"
ls -lh "$SEED_DIR"/*.sql.gz
