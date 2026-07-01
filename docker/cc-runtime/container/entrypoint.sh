#!/bin/sh
# DMAC Assistant container entrypoint.
#
# Responsibilities:
#   1. Bridge NEXTSEEK_* env vars to chat_nextseek's API_USER/API_PASS names.
#   2. Scrub settings.local.json's env block on every start.
#   3. Hand control to the declared command with exec.

set -eu

# D20: chat_nextseek's ChatConfig reads API_USER / API_PASS.
: "${API_USER:=${NEXTSEEK_USERNAME:-}}"
: "${API_PASS:=${NEXTSEEK_PASSWORD:-}}"
: "${NEXTSEEK_BASE_URL:=${NEXTSEEK_URL:-}}"
# D23: GCP-only profile.
: "${NEXTSEEK_MODE:=gcp}"
export API_USER API_PASS NEXTSEEK_BASE_URL NEXTSEEK_MODE

# Backward compat: SEEK_USER / SEEK_PASSWORD still exported for any host-side
# tooling that grew up reading them. Removable post-Plan-B once nothing
# downstream depends on them.
: "${SEEK_USER:=$API_USER}"
: "${SEEK_PASSWORD:=$API_PASS}"
export SEEK_USER SEEK_PASSWORD

SETTINGS_PATH="${ENTRYPOINT_SETTINGS_PATH:-/home/user/.claude/settings.local.json}"

_scrub_env_block() {
  _tmp=''

  if _has_env="$(jq 'has("env")' "$SETTINGS_PATH")"; then
    if [ "$_has_env" = "false" ]; then
      return 0
    fi
  else
    printf '%s\n' 'entrypoint: failed to parse settings.local.json' >&2
    return 1
  fi

  if ! _mode="$(stat -c '%a' "$SETTINGS_PATH" 2>/dev/null || stat -f '%Lp' "$SETTINGS_PATH" 2>/dev/null)"; then
    printf '%s\n' 'entrypoint: failed to stat settings.local.json' >&2
    return 1
  fi

  _tmp="${SETTINGS_PATH}.scrub.$$"

  if ! jq 'del(.env)' "$SETTINGS_PATH" >"$_tmp"; then
    rm -f "$_tmp"
    printf '%s\n' 'entrypoint: failed to scrub env from settings.local.json' >&2
    return 1
  fi

  chmod "$_mode" "$_tmp"
  mv "$_tmp" "$SETTINGS_PATH"
}

if [ -f "$SETTINGS_PATH" ]; then
  _scrub_env_block
fi

# DD-37 part A: ensure the image-baked CLAUDE.md is discoverable from the
# WORKDIR (claude-code reads CLAUDE.md from cwd, not from /app/). The
# Dockerfile already creates this symlink at build time; re-create it
# defensively in case a future mount obliterates it. Failure is non-fatal —
# claude-code will just lack plugin guidance.
CLAUDE_MD_SOURCE="${ENTRYPOINT_CLAUDE_MD_SOURCE:-/app/CLAUDE.md}"
CLAUDE_MD_LINK="${ENTRYPOINT_CLAUDE_MD_LINK:-/home/user/CLAUDE.md}"
if [ ! -e "$CLAUDE_MD_LINK" ] && [ -f "$CLAUDE_MD_SOURCE" ]; then
  ln -sfn "$CLAUDE_MD_SOURCE" "$CLAUDE_MD_LINK" 2>/dev/null || true
fi

# DD-37 part B: claude-code only auto-discovers plugins (skills, commands)
# under ~/.claude/plugins/local/. /app/plugins/ is a parking spot for the
# image-baked plugin tree; symlink it into the runtime-mounted .claude/
# tree so claude-code actually loads the SKILL.md, the slash command, and
# the bin/ scripts as a registered plugin. The nextseek-api path remains a
# legacy host-side convention for compatibility.
PLUGIN_SRC_ROOT="${ENTRYPOINT_PLUGIN_SRC_ROOT:-/app/plugins}"
PLUGIN_LINK_ROOT="${ENTRYPOINT_PLUGIN_LINK_ROOT:-/home/user/.claude/plugins/local}"
if [ -d "$PLUGIN_SRC_ROOT" ]; then
  mkdir -p "$PLUGIN_LINK_ROOT" 2>/dev/null || true
  for _plugin_dir in "$PLUGIN_SRC_ROOT"/*; do
    [ -d "$_plugin_dir" ] || continue
    _plugin_name="$(basename "$_plugin_dir")"
    _plugin_link="$PLUGIN_LINK_ROOT/$_plugin_name"
    if [ ! -e "$_plugin_link" ]; then
      ln -sfn "$_plugin_dir" "$_plugin_link" 2>/dev/null || true
    fi
  done
fi

# DD-04 (LLM router): idle-mode keeps the container alive after pre-flight so
# ws.py can issue per-turn `docker exec` for either route. The branch runs
# AFTER all pre-flight (scrub, symlinks) — Risk #2 forbids the inverse order.
# Only the literal value "idle" triggers idle mode; any other value (including
# unset / empty / capitalization variants) preserves the original exec "$@".
if [ "${DMAC_RUNTIME_MODE:-}" = "idle" ]; then
  exec sleep infinity
fi

exec "$@"
