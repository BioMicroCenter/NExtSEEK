#!/bin/sh
# Layer 1 — install permission allowlist into ~/.claude/settings.json (idempotent).
# Pre-allows GET-only nextseek-* shims and structurally-safe shims (including the
# read-only nextseek-batch-upload build/validate shims, which each hard-refuse
# --start/--upload/--confirmed-write internally and never write to NExtSEEK).
# Anything outside the allowlist (including --confirmed-write) trips a
# Claude Code permission prompt CC cannot bypass.
set -eu

SETTINGS="${SETTINGS_FILE:-$HOME/.claude/settings.json}"
mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"

ALLOW='[
  "Bash(nextseek-entity-extract:*)",
  "Bash(nextseek-parse:*)",
  "Bash(nextseek-plan:*)",
  "Bash(nextseek-api-read --parser-plan*)",
  "Bash(nextseek-graph:*)",
  "Bash(nextseek-report --mode samples*)",
  "Bash(nextseek-report --mode protocols*)",
  "Bash(nextseek-report --mode published*)",
  "Bash(nextseek-report --mode rppr*)",
  "Bash(nextseek-generate-submission --type*)",
  "Bash(nextseek-pipeline:*)",
  "Bash(nextseek-run-ls:*)",
  "Bash(nextseek-build-upload-xlsx:*)",
  "Bash(nextseek-project-resolve:*)",
  "Bash(nextseek-sampletype-attrs:*)",
  "Bash(nextseek-sample-search:*)",
  "Bash(nextseek-assay-resolve:*)",
  "Bash(nextseek-build-payload:*)",
  "Bash(nextseek-validate-upload:*)",
  "Bash(nextseek-extract-text:*)"
]'

jq --argjson new "$ALLOW" '
  .permissions //= {} |
  .permissions.allow //= [] |
  .permissions.allow = (.permissions.allow + $new | unique)
' "$SETTINGS" > "$SETTINGS.tmp" && mv "$SETTINGS.tmp" "$SETTINGS"

echo "nextseek allowlist installed at $SETTINGS"
