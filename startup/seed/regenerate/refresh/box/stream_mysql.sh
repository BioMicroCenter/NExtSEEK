# Runs ON THE BOX as the stack's service account (fetch.sh sends it). Streams one schema's consistent online dump
# (--single-transaction) to stdout, gzipped; the box-side sha256 goes to stderr. Nothing is written on the box.
# fetch.sh prepends: SCHEMA=..., BOX_PREFIX=...
set -euo pipefail
docker exec "${BOX_PREFIX}seek-mysql" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --routines --triggers --default-character-set=utf8mb4 '"$SCHEMA" \
  | gzip -1 | tee >(sha256sum | sed "s/-\$/box-sha256 $SCHEMA/" >&2)
