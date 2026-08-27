#!/bin/sh
# Sourced by every nextseek-* shim. Translates env vars chat_nextseek expects.

# D20: re-export env names chat_nextseek's ChatConfig reads.
: "${API_USER:=${NEXTSEEK_USERNAME:-}}"
: "${API_PASS:=${NEXTSEEK_PASSWORD:-}}"
: "${NEXTSEEK_BASE_URL:=${NEXTSEEK_URL:-}}"
export API_USER API_PASS NEXTSEEK_BASE_URL

# D23: force GCP profile.
: "${NEXTSEEK_MODE:=gcp}"
export NEXTSEEK_MODE

# Default outputs land under /data/scratch/<user>/<run-id>/ (chat_nextseek
# orchestrator creates per-run subdirs). The bridge-side copier (Plan A T5)
# discovers them by scratch-listing diff and publishes to /data/output/.
: "${NEXTSEEK_OUTPUTS_DIR:=/data/scratch/${API_USER:-anon}}"
export NEXTSEEK_OUTPUTS_DIR

# Quiet config-load logs by default.
: "${CHAT_NEXTSEEK_CONFIG_VERBOSE:=false}"
export CHAT_NEXTSEEK_CONFIG_VERBOSE

# nextseek_die <code> <message>: structured error -> stderr + exit
nextseek_die() {
  printf 'nextseek-error: %s\n' "$2" >&2
  exit "$1"
}
