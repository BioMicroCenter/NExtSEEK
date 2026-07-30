---
name: nextseek-issues
description: Use when working in the NExtSEEK codebase and (a) you find a bug or defect whose fix is deferred or out of the current task's scope, (b) a plan or task completes with residuals, follow-ups, or accepted debt, or (c) anyone asks to file/create a NExtSEEK GitHub issue. Instead of silently letting deferred work languish, draft a conventions-conforming GitHub issue and ASK the user to approve filing it.
---

# NExtSEEK structured issues

**Read `docs/ISSUE-CONVENTIONS.md` first** — it defines the type enum, area
labels, the 7-section description schema, epistemic tags, and safety rules.
This skill only holds the workflow and the hard gates.

## Hard rules

1. **NEVER file, edit, or label a GitHub issue without explicit human
   approval in this conversation.** The repo is PUBLIC and issues are
   outward-facing. Read-only `gh issue list/view` and `gh label list` are
   always fine.
2. **Never include secrets or credential values** in a draft. The validator
   scans, but you are the first line.
3. **Root cause is never guessed.** If it is not established with evidence,
   use the sentinel line the conventions define.

## Workflow

1. **Draft** the issue to a local scratch file: YAML frontmatter
   (`title`, `type`, `areas`, optional `priority`, optional `needs_ruling`)
   + the seven `##` sections per the conventions doc. Copy the structure of
   the worked example in `docs/ISSUE-CONVENTIONS.md`.
2. **Validate** until clean:
   `uv run python scripts/validate_issue.py <draft.md> --labels`
   (On the NExtSEEK dev box use the repo-mounted container form documented
   in CLAUDE.md instead of bare `uv run`.)
3. **Duplicate-check:** `gh issue list --repo BMCBCC/NExtSEEK --state all
   --search "<key behavior words>"`. If a matching issue exists, propose
   commenting/relabeling it instead of filing a duplicate.
4. **Present the full draft** (title, labels, body) to the user and ask
   whether to file it. Do not proceed on silence.
5. **On approval:** ensure the labels exist (`gh label list`; if a new
   `area:` label is needed, create it AND add its covers-line to
   `docs/ISSUE-CONVENTIONS.md` in the same session), then:
   `gh issue create --repo BMCBCC/NExtSEEK --title "<title>"
   --label "<label1>" --label "<label2>" --body-file <draft-body.md>`
   (body = the draft minus its frontmatter). Report the issue URL.

## When you find deferred work mid-task

Do not wait for the user to ask. Say what you found, why it is out of scope
right now, and offer to draft the issue — then follow the workflow above.
