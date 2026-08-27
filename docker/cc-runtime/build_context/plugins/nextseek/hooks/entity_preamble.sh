#!/bin/sh
# UserPromptSubmit hook — ALWAYS resolve NExtSEEK vocabulary before the agent acts.
#
# Runs nextseek-entity-extract on the user's prompt OUTSIDE the agent's control and
# injects the resolved {sampletypes, assays, keywords, projects} as additionalContext,
# so downstream op calls (graph / api-read / report / ...) are grounded in NExtSEEK
# vocabulary and abbreviations are expanded (e.g. "GBM" -> the Glioblastoma
# investigation) BEFORE the agent constructs a query.
#
# Isolation (OI-3): reuses the existing bin -> sidecar path only. No new credentials,
# no new network, scratch-only. Fail-OPEN: any missing prompt, missing bin, timeout,
# or malformed result emits nothing and the turn proceeds normally — the hook can
# never block or break a turn.
set -eu

INPUT="$(cat)"

# UserPromptSubmit carries the prompt as .prompt (older builds: .user_prompt).
PROMPT="$(printf '%s' "$INPUT" | jq -r '.prompt // .user_prompt // empty' 2>/dev/null || true)"
[ -n "${PROMPT:-}" ] || exit 0

BIN=/app/plugins/nextseek/bin/nextseek-entity-extract
[ -x "$BIN" ] || exit 0

# Bounded so a slow/failed resolve never stalls the turn.
RESOLVED="$(timeout 25 "$BIN" --query "$PROMPT" 2>/dev/null || true)"

# Only inject if entity-extract returned non-empty valid JSON.
printf '%s' "$RESOLVED" | jq -e . >/dev/null 2>&1 || exit 0

printf '%s' "$RESOLVED" | jq -c '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: (
      "NExtSEEK vocabulary auto-resolved for this query (nextseek-entity-extract ran automatically before you act). Use these canonical terms — not the raw phrasing/abbreviations — when building op calls (investigation/project names, sampletype codes, assays, keywords). If a term you need is not here, consult context/MANIFEST.md:\n"
      + tojson
    )
  }
}' 2>/dev/null || exit 0
