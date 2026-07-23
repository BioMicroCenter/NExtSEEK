---
description: NExtSEEK data workflow. Routes via the nextseek skill.
allowed-tools: Bash, Read
---

# /nextseek

You have been invoked via the `/nextseek` slash command. Use the `nextseek` skill (auto-loads from `skills/nextseek/SKILL.md`), which documents the NExtSEEK ops and when to use each.

The user's question is below the `---`. Pick the right op(s) for the task per SKILL.md: a search is `nextseek-parse` then `nextseek-api-read`; lineage is `nextseek-graph`; a project report is `nextseek-report`; a submission is `nextseek-generate-submission`; a multi-step "do X, then Y" request is `nextseek-plan`; a create/update/delete is `nextseek-parse` then `nextseek-api-write` under the Layer-3 plain-text confirmation; a single-shot NS run in the live chat session is `nextseek-query`; to reuse raw rows from a prior turn use `nextseek-recall --turn N` instead of re-querying. Compose the answer from what the op(s) return.

---

$ARGUMENTS
