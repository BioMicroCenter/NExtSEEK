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

# The client that writes the seed: the host's `mysqldump` unless MYSQLDUMP names another command, e.g. the
# MySQL container's own client, `MYSQLDUMP="docker exec <mysql container> mysqldump"` with
# MYSQL_HOST_DEV=127.0.0.1 and MYSQL_PORT=3306 (no published port needed). The old seeds were written by an
# Oracle MySQL 8 client, the same one install loads them with.
read -r -a DUMP <<< "${MYSQLDUMP:-mysqldump}"

# A MariaDB 10.5.25+/11.x client starts every dump with `/*M!999999\- enable the sandbox mode */`, a line the
# MySQL 8 client that install loads the seed with rejects ("Unknown command '\-'"). Drop exactly that line.
strip_sandbox() { sed '1{/^\/\*M!999999\\- enable the sandbox mode \*\/$/d}'; }

# R7 (startup/tests/test_seed_charset.py): the dmac seed is utf8mb4, so a fresh install's chat tables take any text.
# A box whose dmac database was created latin1 dumps latin1 table defaults, plus explicit latin1 on the child
# columns that must match a latin1 parent key (assistant_chat_session.session_id). Rewrite both, on schema lines
# only (column definitions and table options), never on a data row; rows are written as utf8mb4 either way.
dmac_utf8mb4() {
  sed -E \
    -e '/^\) ENGINE=/ s/ DEFAULT CHARSET=latin1( COLLATE=latin1_[a-z0-9_]+)?/ DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci/' \
    -e '/^  `/ s/ CHARACTER SET latin1 COLLATE latin1_[a-z0-9_]+/ CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci/g'
}

BASE_ARGS=(
  -h "$MYSQL_HOST_DEV" -P "$MYSQL_PORT"
  -u "$MYSQL_USER" -p"$MYSQL_DEV_PASSWORD"
  --single-transaction --quick
  --default-character-set=utf8mb4
)

# `--column-statistics` is an Oracle MySQL 8 client option that MariaDB's
# mysqldump does not implement at all -- it exits 7 with "unknown variable"
# rather than ignoring it. Production's host client is MariaDB while the server
# and the containerised client are Oracle MySQL, so the option is valid for the
# client shipped *inside* the container and invalid for the one this script
# actually invokes on the host. Probe the client we are about to run instead of
# assuming a vendor.
if "${DUMP[@]}" --help 2>/dev/null | grep -q -- '--column-statistics'; then
  BASE_ARGS+=(--column-statistics=0)
fi

# Never redirect straight onto the committed seed. `> "$dest"` truncates the
# target before mysqldump is even execed, so any non-zero exit destroys the
# artifact the operator is trying to refresh -- `set -e` aborts the loop but
# cannot un-truncate a file. Build into a temp beside the destination and move
# it into place only after the whole pipeline succeeds.
TMP_FILES=()
cleanup_tmp() {
  if (( ${#TMP_FILES[@]} )); then
    rm -f "${TMP_FILES[@]}"
  fi
}
trap cleanup_tmp EXIT

for schema in dmac seek_production; do
  echo "dumping $schema -> $SEED_DIR/${schema}.sql.gz"
  dest="$SEED_DIR/${schema}.sql.gz"
  tmp="${dest}.tmp.$$"
  TMP_FILES+=("$tmp")
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
      "${DUMP[@]}" "${BASE_ARGS[@]}" --routines --triggers \
        --ignore-table="${schema}.settings" "$schema" | strip_sandbox
      "${DUMP[@]}" "${BASE_ARGS[@]}" \
        --where="var <> 'site_base_host'" "$schema" settings | strip_sandbox
    } | gzip > "$tmp"
  else
    "${DUMP[@]}" "${BASE_ARGS[@]}" --routines --triggers "$schema" \
      | strip_sandbox | dmac_utf8mb4 | gzip > "$tmp"
  fi
  mv "$tmp" "$dest"
done

echo "done. Files:"
ls -lh "$SEED_DIR"/*.sql.gz
