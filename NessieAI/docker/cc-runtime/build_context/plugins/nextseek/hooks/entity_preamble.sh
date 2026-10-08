#!/bin/sh
# UserPromptSubmit hook: runs before the agent acts on EVERY turn, resumed or not, and injects
# two notes as additionalContext, outside the agent's control:
#
# 1. The newest staged turn of this chat (from /data/previous_turns/manifest.json, mounted
#    read-only when the chat has answered turns): which turn, what it asked, what it returned,
#    whether it carries sample UIDs, which files hold it, and to read MANIFEST.md first. A
#    resumed conversation did not look at the staged turns again and answered about the wrong
#    turn or redid its own (CC-RERUN-FINDINGS fix 3).
# 2. The NExtSEEK vocabulary Django resolved for the user's question before the turn started
#    ({sampletypes, assays, keywords, projects, ...}), so op calls use canonical terms and
#    abbreviations are expanded (e.g. "GBM" -> the Glioblastoma investigation). Django writes it
#    to /data/turn/vocabulary.json, the turn's own read-only mount.
#
# "post" mode (PostToolUse): the vocabulary can be written while the container runs, when the
# pre-run is slower than the start's wait. After a tool call, if the prompt run did not already
# inject it and /data/turn/vocabulary.json is valid now, the same note is added once. The marker is
# the container's own /tmp (a new container per turn): touching it only withholds the note from the
# agent itself.
#
# Isolation (OI-3): reads two read-only mounts only. No credentials, no network, no bin call.
# Fail-OPEN: a missing or malformed manifest or vocabulary file drops that note; with neither
# note the hook emits nothing, and it can never block or break a turn.
set -eu

MODE="${1:-prompt}"
MARKER="${NEXTSEEK_VOCAB_MARKER:-/tmp/nextseek-vocab-injected}"
[ "$MODE" = post ] && [ -e "$MARKER" ] && { cat >/dev/null || true; exit 0; }

cat >/dev/null || true   # the prompt on stdin is not needed: the vocabulary is the turn's own

# The two paths can be pointed elsewhere for the tests; the container sets neither.
PREV_DIR="${NEXTSEEK_PREVIOUS_TURNS_DIR:-/data/previous_turns}"
VOCAB_FILE="${NEXTSEEK_TURN_VOCABULARY_FILE:-/data/turn/vocabulary.json}"

# 1. The newest staged turn (manifest turns are newest first).
PREV=""
if [ "$MODE" != post ] && [ -r "$PREV_DIR/manifest.json" ]; then
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

# 2. The vocabulary, when Django had it before the container started.
VOCAB=""
WHEN="before your turn started"
[ "$MODE" = post ] && WHEN="while your turn was starting"
if [ -r "$VOCAB_FILE" ] && jq -e . "$VOCAB_FILE" >/dev/null 2>&1; then
  VOCAB="$(jq -r --arg when "$WHEN" '
    "NExtSEEK vocabulary auto-resolved for this query (NExtSEEK resolved it \($when); do not run nextseek-entity-extract again for it). Use these canonical terms, not the raw phrasing or abbreviations, when building op calls (investigation/project names, sampletype codes, assays, keywords). If a term you need is not here, consult context/MANIFEST.md:\n"
    + tojson
  ' "$VOCAB_FILE" 2>/dev/null || true)"
fi

if [ "$MODE" = post ]; then
  [ -n "$VOCAB" ] || exit 0
  jq -n -c --arg vocab "$VOCAB" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $vocab}}' \
    2>/dev/null && : >"$MARKER" 2>/dev/null || true
  exit 0
fi

[ -n "$PREV$VOCAB" ] || exit 0
[ -z "$VOCAB" ] || : >"$MARKER" 2>/dev/null || true

jq -n -c --arg prev "$PREV" --arg vocab "$VOCAB" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: ([$prev, $vocab] | map(select(. != "")) | join("\n\n"))
  }
}' 2>/dev/null || exit 0
