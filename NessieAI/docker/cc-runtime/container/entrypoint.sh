#!/bin/sh
# DMAC Assistant container entrypoint.
#
# Responsibilities:
#   1. Bridge NEXTSEEK_* env vars to chat_nextseek's API_USER/API_PASS names.
#   2. Rebuild ~/.claude from the image: delete everything a turn left there except the
#      memory CLAUDE.md and this chat's --resume store, then install the baked settings
#      files, the plugin link, the allow list (setup.sh) and the entity hook.
#   3. Check that the installed allow list and hooks equal the image's baked copy.
#   4. Hand control to the declared command with exec.

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

# ~/.claude is the chat's cc-state folder: kept across the chat's turns and writable by
# the agent. What one turn writes there (settings, hooks, skills, agents, commands,
# plugins, memory folders) must not reach the next turn, so every start rebuilds it.
# Kept: CLAUDE.md (the memory file Django writes before each turn) and, under
# projects/<cwd>/, the session transcripts (<uuid>.jsonl) and session folders (<uuid>/:
# tool results and subagent transcripts the conversation refers to), which --resume
# needs. Everything else goes; a link is removed, never followed.
CLAUDE_HOME="${ENTRYPOINT_CLAUDE_HOME:-$HOME/.claude}"
BAKED_HOME="${ENTRYPOINT_BAKED_HOME:-/app/claude-home}"
SETTINGS_PATH="${ENTRYPOINT_SETTINGS_PATH:-$CLAUDE_HOME/settings.local.json}"
# Claude Code keeps a working folder's conversations under projects/<the cwd with every
# character outside [A-Za-z0-9] replaced by '-'> (/home/user -> -home-user).
CWD_SLUG="$(printf '%s' "${ENTRYPOINT_CC_CWD:-$PWD}" | sed 's/[^A-Za-z0-9]/-/g')"
_UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

_is_uuid() {
  printf '%s\n' "$1" | grep -Eq "$_UUID_RE"
}

# Delete every entry of folder $1 that the keep test $2 rejects; stop at the first failure.
_prune() {
  for _entry in "$1"/* "$1"/.[!.]* "$1"/..?*; do
    [ -e "$_entry" ] || [ -L "$_entry" ] || continue
    if "$2" "$_entry"; then
      continue
    fi
    rm -rf -- "$_entry" || return 1
  done
}

_keep_in_home() {
  [ ! -L "$1" ] || return 1
  case "${1##*/}" in
    CLAUDE.md) [ -f "$1" ] ;;
    projects) [ -d "$1" ] ;;
    *) return 1 ;;
  esac
}

_keep_in_projects() {
  [ ! -L "$1" ] && [ -d "$1" ] && [ "${1##*/}" = "$CWD_SLUG" ]
}

_keep_in_store() {
  [ ! -L "$1" ] || return 1
  _name="${1##*/}"
  case "$_name" in
    *.jsonl) [ -f "$1" ] && _is_uuid "${_name%.jsonl}" ;;
    *) [ -d "$1" ] && _is_uuid "$_name" ;;
  esac
}

_reset_claude_home() {
  mkdir -p "$CLAUDE_HOME" || return 1
  _prune "$CLAUDE_HOME" _keep_in_home || return 1
  if [ -d "$CLAUDE_HOME/projects" ]; then
    _prune "$CLAUDE_HOME/projects" _keep_in_projects || return 1
  fi
  if [ -d "$CLAUDE_HOME/projects/$CWD_SLUG" ]; then
    _prune "$CLAUDE_HOME/projects/$CWD_SLUG" _keep_in_store || return 1
  fi
  cp "$BAKED_HOME/settings.json" "$CLAUDE_HOME/settings.json" || return 1
  cp "$BAKED_HOME/settings.local.json" "$SETTINGS_PATH" || return 1
}

# Fail closed: a start that cannot rebuild ~/.claude would run with what a turn left there.
if ! _reset_claude_home; then
  printf '%s\n' 'entrypoint: could not rebuild ~/.claude from the image; refusing to start' >&2
  exit 1
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
# image-baked plugin tree; link each plugin into the rebuilt ~/.claude at every
# start (the reset above removed whatever a turn left under plugins/).
PLUGIN_SRC_ROOT="${ENTRYPOINT_PLUGIN_SRC_ROOT:-/app/plugins}"
PLUGIN_LINK_ROOT="${ENTRYPOINT_PLUGIN_LINK_ROOT:-$CLAUDE_HOME/plugins/local}"
if [ -d "$PLUGIN_SRC_ROOT" ]; then
  mkdir -p "$PLUGIN_LINK_ROOT" 2>/dev/null || true
  for _plugin_dir in "$PLUGIN_SRC_ROOT"/*; do
    [ -d "$_plugin_dir" ] || continue
    _plugin_name="$(basename "$_plugin_dir")"
    _plugin_link="$PLUGIN_LINK_ROOT/$_plugin_name"
    rm -rf -- "$_plugin_link" 2>/dev/null || true
    ln -s "$_plugin_dir" "$_plugin_link" 2>/dev/null || true
  done
fi

# Write-safety Layer 1 (OI / "no security regressions"): install the nextseek
# permission allowlist into ~/.claude/settings.json so it is LOADED when the
# headless agent runs. setup.sh replaces the list (never merges). Fail-open so a
# setup error never blocks startup. MUST run BEFORE the hook block below so both
# land in the same settings.json (hook merge via jq preserves .permissions.allow).
NS_SETUP="${ENTRYPOINT_NS_SETUP:-/app/plugins/nextseek/scripts/setup.sh}"
if [ -x "$NS_SETUP" ]; then
  SETTINGS_FILE="$CLAUDE_HOME/settings.json" sh "$NS_SETUP" >/dev/null 2>&1 || true
fi

# Register the UserPromptSubmit hook that ALWAYS runs nextseek-entity-extract
# before the agent acts (resolve vocabulary / expand abbreviations like GBM),
# outside the agent's control. This is done in the user-level settings.json
# because the headless `claude --print` runtime does NOT load local-plugin
# hooks/hooks.json — only skills/commands are auto-discovered from the plugin
# symlink. Idempotent + fail-open: any error leaves settings untouched and never
# blocks startup. Isolation preserved: the hook only re-invokes the existing bin.
NS_SETTINGS="${ENTRYPOINT_CLAUDE_SETTINGS:-$CLAUDE_HOME/settings.json}"
NS_HOOK="${ENTRYPOINT_NS_HOOK:-/app/plugins/nextseek/hooks/entity_preamble.sh}"
if command -v jq >/dev/null 2>&1 && [ -x "$NS_HOOK" ]; then
  mkdir -p "$(dirname "$NS_SETTINGS")" 2>/dev/null || true
  [ -f "$NS_SETTINGS" ] || echo '{}' > "$NS_SETTINGS"
  _ns_tmp="$NS_SETTINGS.nshook.tmp"
  # The program is the image's entity-hook.jq, the same one the image build used for the baked copy.
  if jq --arg cmd "$NS_HOOK" -f "$BAKED_HOME/entity-hook.jq" "$NS_SETTINGS" > "$_ns_tmp" 2>/dev/null; then
    mv "$_ns_tmp" "$NS_SETTINGS" 2>/dev/null || rm -f "$_ns_tmp"
  else
    rm -f "$_ns_tmp"
  fi
fi

# Start check: the allow list and hooks now installed must equal the image's baked copy
# (expected-settings.json, built at image build from the same baked settings.json,
# setup.sh and entity-hook.jq). The two steps above are fail-open, so without this a
# container could start with no allow list or no entity hook. This is a correctness
# check, not a security boundary: a missing allow list means more permission prompts,
# not fewer, so it closes no hole; it stops a misconfigured start with a clear message
# instead of a turn that fails at its first command.
EXPECTED_SETTINGS="${ENTRYPOINT_EXPECTED_SETTINGS:-$BAKED_HOME/expected-settings.json}"
_settings_match() {
  command -v jq >/dev/null 2>&1 || return 1
  _want="$(jq -cS '{allow: .permissions.allow, hooks: .hooks}' "$EXPECTED_SETTINGS" 2>/dev/null)" || return 1
  _have="$(jq -cS -n --slurpfile a "$CLAUDE_HOME/settings.json" --slurpfile h "$NS_SETTINGS" \
             '{allow: $a[0].permissions.allow, hooks: $h[0].hooks}' 2>/dev/null)" || return 1
  [ -n "$_want" ] && [ "$_want" = "$_have" ]
}
if ! _settings_match; then
  printf '%s\n' "entrypoint: the allow list or hooks in $CLAUDE_HOME/settings.json differ from the image's baked copy ($EXPECTED_SETTINGS); refusing to start" >&2
  exit 1
fi

# DD-04 (LLM router): idle-mode keeps the container alive after pre-flight so
# ws.py can issue per-turn `docker exec` for either route. The branch runs
# AFTER all pre-flight (reset, links) — Risk #2 forbids the inverse order.
# Only the literal value "idle" triggers idle mode; any other value (including
# unset / empty / capitalization variants) preserves the original exec "$@".
if [ "${DMAC_RUNTIME_MODE:-}" = "idle" ]; then
  exec sleep infinity
fi

exec "$@"
