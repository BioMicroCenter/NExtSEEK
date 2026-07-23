#!/usr/bin/env bash
# provision_embedding_model.sh — provision the schema_rag embedding model cache
# for THIS CHECKOUT.
#
# WHAT / WHY
#   nextseek_api/schema_rag/service.py lazily loads
#   SentenceTransformer(settings.SCHEMA_RAG_EMBEDDING_MODEL_NAME) with
#   cache_folder = settings.SCHEMA_RAG_EMBEDDING_MODEL_PATH
#   (= <repo>/schema_rag/embedding_models). That cache directory is
#   GITIGNORED (.gitignore: embedding_models/) and therefore empty in every
#   fresh clone/worktree — provisioning is PER CHECKOUT, not per machine.
#   Without it, the first retrieval call tries to download the model from
#   huggingface.co at runtime (and fails hard in offline/hermetic test lanes).
#
# HOW (seed-first, download-fallback; precedent: startup/seed/ ships local
# seed data and falls back to a network download only when needed)
#   1. If <repo>/schema_rag/embedding_models already matches the manifest
#      (startup/dev/bge-small-en-v1.5.manifest.json, committed next to this
#      script: pinned repo_id + revision + per-file SHA-256s), exit 0.
#      This path is network-free and safe to run inside `--network none`.
#   2. Else, if --seed <dir> is given: verify the seed against the manifest,
#      copy it into the cache, and re-verify the copy. Network-free.
#   3. Else, FALLBACK ONLY: download the pinned revision through the repo's
#      own built image (nextseek-nextseek:latest) using the exact production
#      loader — SentenceTransformer(repo_id, revision=..., cache_folder=...).
#      This is the ONLY network-touching path in this script.
#   Every file is SHA-256-verified against the manifest either way; any
#   mismatch/missing/extra file fails loudly, naming the offending path.
#
# USAGE
#   startup/dev/provision_embedding_model.sh [--seed <dir>] [--target <repo-root>]
#   startup/dev/provision_embedding_model.sh --verify <cache-dir>
#
#   --seed <dir>          local manifest-verified HF-hub-layout cache to copy
#                         from (network-free; preferred over the download)
#   --target <repo-root>  checkout to provision (default: the repo containing
#                         this script); the cache goes to
#                         <repo-root>/schema_rag/embedding_models
#   --verify <cache-dir>  verification-only mode: check an arbitrary cache
#                         dir against the manifest and exit (0 = match).
#                         Used by test-lane pre-flights and audit oracles.
#
# EXIT CODES
#   0  cache matches the manifest (already provisioned, or provisioned now)
#   1  verification failure (offending path printed) or provisioning failure
#   2  usage / environment error

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${SCRIPT_DIR}/bge-small-en-v1.5.manifest.json"

SEED_DIR=""
TARGET_ROOT=""
VERIFY_DIR=""

usage() {
    cat >&2 <<'USAGE'
usage:
  startup/dev/provision_embedding_model.sh [--seed <dir>] [--target <repo-root>]
  startup/dev/provision_embedding_model.sh --verify <cache-dir>

  --seed <dir>          local manifest-verified HF-hub-layout cache to copy
                        from (network-free; preferred over the download)
  --target <repo-root>  checkout to provision (default: the repo containing
                        this script); cache: <repo-root>/schema_rag/embedding_models
  --verify <cache-dir>  verification-only: check a cache dir against the
                        manifest and exit (0 = match)
USAGE
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --seed)   [ $# -ge 2 ] || usage; SEED_DIR="$2"; shift 2 ;;
        --target) [ $# -ge 2 ] || usage; TARGET_ROOT="$2"; shift 2 ;;
        --verify) [ $# -ge 2 ] || usage; VERIFY_DIR="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "ERROR: unknown argument: $1" >&2; usage ;;
    esac
done

if [ ! -f "${MANIFEST}" ]; then
    echo "ERROR: manifest not found: ${MANIFEST}" >&2
    exit 2
fi

PY="$(command -v python3 || command -v python || true)"
if [ -z "${PY}" ]; then
    echo "ERROR: no python3/python interpreter found on PATH" >&2
    exit 2
fi

# verify_cache <cache-dir>
# Prints one "OK <sha256>  <file>" line per manifest file; on any problem
# prints the offending path and returns 1. Also checks refs/<default> points
# at the pinned revision and that the snapshot contains NO extra files.
verify_cache() {
    "${PY}" - "$1" "${MANIFEST}" <<'PYEOF'
import hashlib, os, sys

try:
    import orjson as _json  # repo convention: prefer orjson when available
    _loads = _json.loads
except ImportError:
    import json as _json
    _loads = _json.loads

cache_dir, manifest_path = sys.argv[1], sys.argv[2]
with open(manifest_path, "rb") as fh:
    manifest = _loads(fh.read())

repo_id = manifest["repo_id"]
revision = manifest["revision"]
files = manifest["files"]

model_dir = os.path.join(cache_dir, "models--" + repo_id.replace("/", "--"))
snapshot_dir = os.path.join(model_dir, "snapshots", revision)
ref_path = os.path.join(model_dir, "refs", "main")

failed = False

def fail(msg):
    global failed
    failed = True
    print("VERIFY FAIL: " + msg, file=sys.stderr)

if not os.path.isdir(cache_dir):
    fail("cache dir missing: " + cache_dir)
    sys.exit(1)
if not os.path.isdir(snapshot_dir):
    fail("snapshot dir missing: " + snapshot_dir)
    sys.exit(1)

# refs/main must pin the manifest revision (HF hub resolves 'main' via it)
if not os.path.isfile(ref_path):
    fail("ref missing: " + ref_path)
else:
    with open(ref_path, "r") as fh:
        ref = fh.read().strip()
    if ref != revision:
        fail("ref mismatch: %s contains %r, manifest revision is %r"
             % (ref_path, ref, revision))

# per-file SHA-256 (hashing follows the snapshot->blob symlinks)
for rel in sorted(files):
    path = os.path.join(snapshot_dir, rel)
    if not os.path.isfile(path):  # broken symlink or missing file
        fail("missing file: " + path)
        continue
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    if digest != files[rel]:
        fail("hash mismatch: %s (got %s, manifest %s)" % (path, digest, files[rel]))
    else:
        print("OK %s  %s" % (digest, rel))

# no extra files may hide in the pinned snapshot
manifest_set = {os.path.normpath(rel) for rel in files}
for root, _dirs, names in os.walk(snapshot_dir):
    for name in names:
        full = os.path.join(root, name)
        rel = os.path.normpath(os.path.relpath(full, snapshot_dir))
        if rel not in manifest_set:
            fail("extra file not in manifest: " + full)

sys.exit(1 if failed else 0)
PYEOF
}

# ---------------------------------------------------------------------------
# --verify mode: check an arbitrary cache dir and exit
# ---------------------------------------------------------------------------
if [ -n "${VERIFY_DIR}" ]; then
    if [ -n "${SEED_DIR}" ] || [ -n "${TARGET_ROOT}" ]; then
        echo "ERROR: --verify cannot be combined with --seed/--target" >&2
        exit 2
    fi
    if verify_cache "${VERIFY_DIR}"; then
        echo "VERIFIED: ${VERIFY_DIR} matches ${MANIFEST}"
        exit 0
    fi
    echo "FAILED: ${VERIFY_DIR} does not match ${MANIFEST}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# provisioning mode
# ---------------------------------------------------------------------------
if [ -z "${TARGET_ROOT}" ]; then
    TARGET_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
fi
CACHE_DIR="${TARGET_ROOT}/schema_rag/embedding_models"

# 1. already provisioned? (network-free; the only path taken on re-runs)
if verify_cache "${CACHE_DIR}"; then
    echo "ALREADY PROVISIONED: ${CACHE_DIR} matches ${MANIFEST}; nothing to do."
    exit 0
fi
echo "Cache at ${CACHE_DIR} is absent or does not match the manifest; provisioning..."

mkdir -p "${CACHE_DIR}" || { echo "ERROR: cannot create ${CACHE_DIR}" >&2; exit 1; }
# wipe any partial/dirty contents so the copy/download lands clean
if ! find "${CACHE_DIR}" -mindepth 1 -delete; then
    echo "ERROR: could not clear existing contents of ${CACHE_DIR}" >&2
    exit 1
fi

if [ -n "${SEED_DIR}" ]; then
    # 2. seed path (network-free)
    echo "Verifying seed ${SEED_DIR} against ${MANIFEST}..."
    if ! verify_cache "${SEED_DIR}"; then
        echo "ERROR: seed ${SEED_DIR} FAILED manifest verification (see offending path above); refusing to copy." >&2
        exit 1
    fi
    echo "Seed verified; copying into ${CACHE_DIR}..."
    if ! cp -a "${SEED_DIR}/." "${CACHE_DIR}/"; then
        echo "ERROR: copy from ${SEED_DIR} to ${CACHE_DIR} failed" >&2
        exit 1
    fi
else
    # 3. download FALLBACK (the ONLY network-touching path): pinned revision,
    #    fetched through the repo's own built image with the exact production
    #    loader (sentence_transformers), never a hand-rolled downloader.
    REPO_ID="$("${PY}" -c 'import sys
try:
    import orjson as j
except ImportError:
    import json as j
print(j.loads(open(sys.argv[1], "rb").read())["repo_id"])' "${MANIFEST}")"
    REVISION="$("${PY}" -c 'import sys
try:
    import orjson as j
except ImportError:
    import json as j
print(j.loads(open(sys.argv[1], "rb").read())["revision"])' "${MANIFEST}")"
    echo "No --seed given; falling back to network download of ${REPO_ID}@${REVISION} via nextseek-nextseek:latest..."
    if ! command -v docker >/dev/null 2>&1; then
        echo "ERROR: docker not available for the download fallback; re-run with --seed <dir>" >&2
        exit 1
    fi
    if ! docker run --rm \
        --user "$(id -u):$(id -g)" \
        -e HOME=/tmp -e HF_HOME=/tmp/hf \
        -v "${CACHE_DIR}:/provision_cache" \
        --entrypoint /app/.venv/bin/python \
        nextseek-nextseek:latest \
        -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${REPO_ID}', revision='${REVISION}', cache_folder='/provision_cache')"; then
        echo "ERROR: model download via nextseek-nextseek:latest failed" >&2
        exit 1
    fi
fi

# either way: the provisioned cache must match the manifest exactly
if verify_cache "${CACHE_DIR}"; then
    echo "PROVISIONED: ${CACHE_DIR} matches ${MANIFEST}"
    exit 0
fi
echo "ERROR: provisioned cache ${CACHE_DIR} FAILED manifest verification (see offending path above)" >&2
exit 1
