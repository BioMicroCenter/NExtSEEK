# Runs ON THE BOX (fetch.sh sends it). Streams the graph export (graph_export.py, run by the app container's own
# python and neo4j driver) to stdout, gzipped; counts, DDL lines and the box-side sha256 go to stderr.
# fetch.sh prepends: PYB64=<base64 of graph_export.py>, BOX_PREFIX=...  The script is an argument: nothing reads stdin.
set -euo pipefail
PY="$(echo "$PYB64" | base64 -d)"
docker exec "${BOX_PREFIX}nextseek" /app/.venv/bin/python -c "$PY" | gzip -1 | tee >(sha256sum | sed 's/-$/box-sha256 graph/' >&2)
