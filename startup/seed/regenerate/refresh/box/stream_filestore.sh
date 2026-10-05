# Runs ON THE BOX (fetch.sh sends it). Streams the SEEK filestore volume as a gzipped tar from the running seek
# container (read only); the box-side sha256 goes to stderr. fetch.sh prepends: BOX_PREFIX=...
set -euo pipefail
docker exec "${BOX_PREFIX}seek" tar -C /seek/filestore -cf - . | gzip -1 | tee >(sha256sum | sed 's/-$/box-sha256 filestore/' >&2)
