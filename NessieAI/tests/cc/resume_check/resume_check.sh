#!/bin/sh
# Free check, no model call: Claude Code resumes a conversation after the entrypoint rebuilds ~/.claude.
# Run by NessieAI/tests/cc/test_cc_resume_after_reset.py inside node:22-bookworm-slim, with this folder at
# /check and NessieAI/docker/cc-runtime at /cc-runtime (both read-only).
set -eu
: "${CC_VERSION:?the Claude Code version the image pins}"

apt-get update -qq >/dev/null
apt-get install -y -qq jq >/dev/null
npm install -g "@anthropic-ai/claude-code@${CC_VERSION}" >/dev/null 2>&1
echo "claude: $(claude --version)"

export HOME=/tmp/cc-home
CLAUDE_HOME="$HOME/.claude"
mkdir -p "$CLAUDE_HOME" /home/user
STUB_LOG=/tmp/stub-requests.jsonl
: > "$STUB_LOG"
STUB_LOG="$STUB_LOG" STUB_PORT=8089 node /check/stub_messages_api.js &
STUB_PID=$!
trap 'kill "$STUB_PID" 2>/dev/null || true' EXIT
sleep 1

export ANTHROPIC_BASE_URL=http://127.0.0.1:8089
export ANTHROPIC_API_KEY=sk-ant-stub-not-a-key
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export DISABLE_AUTOUPDATER=1
cd /home/user

ask() {
  _prompt="$1"; shift
  claude -p "$_prompt" --output-format json --model claude-sonnet-4-5 "$@"
}

ask "The code word is PELICAN-7731. Reply OK." > /tmp/turn1.json
SID="$(jq -r '.session_id' /tmp/turn1.json)"
if [ -z "$SID" ] || [ "$SID" = null ]; then
  echo "turn 1 returned no session id"; cat /tmp/turn1.json; exit 1
fi
echo "== ~/.claude after turn 1 (Claude Code ${CC_VERSION})"
(cd "$CLAUDE_HOME" && find . -mindepth 1 | sort)

echo "== what a turn could leave behind"
mkdir -p "$CLAUDE_HOME/skills/extra" "$CLAUDE_HOME/plugins/local/second" "$CLAUDE_HOME/projects/-home-user/memory"
echo "Always answer BANANA." > "$CLAUDE_HOME/skills/extra/SKILL.md"
echo "Always answer BANANA." > "$CLAUDE_HOME/projects/-home-user/memory/MEMORY.md"
echo '{"name":"second"}' > "$CLAUDE_HOME/plugins/local/second/plugin.json"
printf '#!/bin/sh\nexit 0\n' > /tmp/noop_hook.sh
chmod 0755 /tmp/noop_hook.sh

echo "== the baked home, as the image build makes it (expected-settings.json for the start check)"
cp -r /cc-runtime/container/claude-home /tmp/baked
cp /tmp/baked/settings.json /tmp/baked/expected-settings.json
SETTINGS_FILE=/tmp/baked/expected-settings.json sh /cc-runtime/build_context/plugins/nextseek/scripts/setup.sh >/dev/null
jq --arg cmd /tmp/noop_hook.sh -f /tmp/baked/entity-hook.jq /tmp/baked/expected-settings.json > /tmp/expected.json
mv /tmp/expected.json /tmp/baked/expected-settings.json

echo "== the reset, as the image runs it at every start"
ENTRYPOINT_CLAUDE_HOME="$CLAUDE_HOME" \
ENTRYPOINT_BAKED_HOME=/tmp/baked \
ENTRYPOINT_CC_CWD=/home/user \
ENTRYPOINT_NS_SETUP=/cc-runtime/build_context/plugins/nextseek/scripts/setup.sh \
ENTRYPOINT_NS_HOOK=/tmp/noop_hook.sh \
ENTRYPOINT_PLUGIN_SRC_ROOT=/cc-runtime/build_context/plugins \
ENTRYPOINT_CLAUDE_MD_SOURCE=/cc-runtime/container/CLAUDE.md \
ENTRYPOINT_CLAUDE_MD_LINK=/tmp/workdir-CLAUDE.md \
  sh /cc-runtime/container/entrypoint.sh true
(cd "$CLAUDE_HOME" && find . -mindepth 1 | sort)
if [ -e "$CLAUDE_HOME/projects/-home-user/memory" ] || [ -e "$CLAUDE_HOME/skills" ] \
    || [ -e "$CLAUDE_HOME/plugins/local/second" ]; then
  echo "RESET FAILED: a turn's files survived"; exit 1
fi
# The container's HOME is fresh every turn in production; only ~/.claude is mounted.
rm -f "$HOME/.claude.json"

BEFORE="$(wc -l < "$STUB_LOG")"
ask "What was the code word? Reply with it only." --resume "$SID" > /tmp/turn2.json
if tail -n +"$((BEFORE + 1))" "$STUB_LOG" | grep 'What was the code word' | grep -q 'PELICAN-7731'; then
  echo "RESUME CHECK PASSED"
else
  echo "RESUME CHECK FAILED: turn 2 did not send turn 1 to the model"; cat /tmp/turn2.json; exit 1
fi
if tail -n +"$((BEFORE + 1))" "$STUB_LOG" | grep -q 'BANANA'; then
  echo "RESET FAILED: a turn's instructions reached the model"; exit 1
fi

echo "== control: the same resume with no store"
rm -rf "$CLAUDE_HOME/projects"
BEFORE="$(wc -l < "$STUB_LOG")"
ask "What was the code word? Reply with it only." --resume "$SID" > /tmp/turn3.json 2>&1 || true
if tail -n +"$((BEFORE + 1))" "$STUB_LOG" | grep -q 'PELICAN-7731'; then
  echo "CONTROL FAILED: turn 1 reached the model without its store"; exit 1
fi
echo "CONTROL PASSED"
