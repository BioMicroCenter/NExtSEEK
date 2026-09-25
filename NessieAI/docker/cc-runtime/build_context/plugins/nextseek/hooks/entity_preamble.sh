#!/bin/sh
# UserPromptSubmit hook: runs before the agent acts on EVERY turn, resumed or not, and injects
# two notes as additionalContext, outside the agent's control:
#
# 1. The newest staged turn of this chat (from /data/previous_turns/manifest.json, mounted
#    read-only when the chat has answered turns): which turn, what it asked, what it returned,
#    whether it carries sample UIDs, which files hold it, and to read MANIFEST.md first. A
#    resumed conversation did not look at the staged turns again and answered about the wrong
#    turn or redid its own (CC-RERUN-FINDINGS fix 3).
# 2. The NExtSEEK vocabulary nextseek-entity-extract resolves for the prompt
#    ({sampletypes, assays, keywords, projects}), so op calls use canonical terms and
#    abbreviations are expanded (e.g. "GBM" -> the Glioblastoma investigation).
#
# Isolation (OI-3): reads the read-only staging mount and reuses the existing bin -> sidecar
# path only. No new credentials, no new network, scratch-only. Fail-OPEN: a missing prompt,
# manifest or bin, a timeout, or malformed output drops that note; with neither note the hook
# emits nothing, and it can never block or break a turn.
set -eu

INPUT="$(cat)"

# The two paths can be pointed elsewhere for the tests; the container sets neither.
PREV_DIR="${NEXTSEEK_PREVIOUS_TURNS_DIR:-/data/previous_turns}"
BIN="${NEXTSEEK_ENTITY_EXTRACT_BIN:-/app/plugins/nextseek/bin/nextseek-entity-extract}"

# 1. The newest staged turn (manifest turns are newest first).
PREV=""
if [ -r "$PREV_DIR/manifest.json" ]; then
  PREV="$(jq -r '
    (.container_path // "/data/previous_turns") as $root
    | (if (.turns | type) == "array" then .turns else [] end) as $turns
    | ($turns[0]) as $t
    | ([$turns[] | select(type == "object" and .route == "nextseek_query")][0]) as $search
    | if ($t | type) != "object" then empty else
        "Newest staged turn of this chat: turn \($t.turn_id) (\($t.route)), which asked \($t.user_query | tojson)."
        + (if $t.route == "container_cc" then
             " It was your own earlier turn: its answer and the files it published are staged, so read them instead of redoing that work."
             + (if $search != null then
                  " The newest NExtSEEK search is turn \($search.turn_id): \($root)/\($search.folder)/."
                else "" end)
           else
             (if $t.count != null then
                " It returned \($t.count) rows"
                + (if $t.truncated == true then " (capped; total \($t.total // "unknown"))" else "" end)
                + "."
              else "" end)
             + (if ($t.sample_uids // 0) > 0 then
                  " Sample UIDs: \($t.sample_uids)."
                elif $t.count != null and $t.count > 0 and $t.has_cypher == true then
                  " It has no sample UIDs (a count or grouped result): the Cypher in search_details.json defines its samples, so to list or break them down change only its RETURN and keep every MATCH and WHERE."
                else "" end)
           end)
        + (if (($t.files // []) | length) > 0
           then " Files in \($root)/\($t.folder)/: \([$t.files[] | .file] | join(", "))."
           else "" end)
        + " A follow-up is about this turn unless the user names another. Read \($root)/MANIFEST.md first, then work from these files"
        + (if $t.route == "nextseek_query" then "; never re-run its search as it was." else "." end)
      end
  ' "$PREV_DIR/manifest.json" 2>/dev/null || true)"
fi

# 2. The vocabulary. UserPromptSubmit carries the prompt as .prompt (older builds: .user_prompt).
VOCAB=""
PROMPT="$(printf '%s' "$INPUT" | jq -r '.prompt // .user_prompt // empty' 2>/dev/null || true)"
if [ -n "${PROMPT:-}" ] && [ -x "$BIN" ]; then
  # Bounded so a slow/failed resolve never stalls the turn.
  RESOLVED="$(timeout 25 "$BIN" --query "$PROMPT" 2>/dev/null || true)"
  # Only inject if entity-extract returned non-empty valid JSON.
  if printf '%s' "$RESOLVED" | jq -e . >/dev/null 2>&1; then
    VOCAB="$(printf '%s' "$RESOLVED" | jq -r '
      "NExtSEEK vocabulary auto-resolved for this query (nextseek-entity-extract ran automatically before you act). Use these canonical terms, not the raw phrasing or abbreviations, when building op calls (investigation/project names, sampletype codes, assays, keywords). If a term you need is not here, consult context/MANIFEST.md:\n"
      + tojson
    ' 2>/dev/null || true)"
  fi
fi

[ -n "$PREV$VOCAB" ] || exit 0

jq -n -c --arg prev "$PREV" --arg vocab "$VOCAB" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: ([$prev, $vocab] | map(select(. != "")) | join("\n\n"))
  }
}' 2>/dev/null || exit 0
