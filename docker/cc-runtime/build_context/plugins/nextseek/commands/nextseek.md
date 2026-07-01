---
description: NExtSEEK query workflow. Routes via the nextseek skill.
allowed-tools: Bash, Read
---

# /nextseek

You have been invoked via the `/nextseek` slash command. Use the `nextseek` skill (auto-loads from `skills/nextseek/SKILL.md`).

The user's question is below the `---`. Default action: a single `nextseek-query --query "<user's full question>"` call. Read the answer's `reply` field and surface it. Do NOT call `nextseek-entity-extract`, `nextseek-parse`, or `nextseek-plan` first — `nextseek-query` runs that pipeline internally. See SKILL.md for routing rules and escape hatches (writes, submissions, debugging).

---

$ARGUMENTS
