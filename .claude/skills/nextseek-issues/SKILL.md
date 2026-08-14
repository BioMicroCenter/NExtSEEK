---
name: nextseek-issues
description: Use when working in the NExtSEEK codebase and (a) you find a bug or defect whose fix is deferred, punted, or out of the current task's scope — including anywhere you would otherwise leave a TODO/FIXME — (b) a plan or task completes with residuals, follow-ups, or accepted debt, or (c) anyone asks to open, file, or create a NExtSEEK GitHub issue or report a bug. Draft a conventions-conforming issue and ASK the user to approve filing it — never file unattended.
---

# NExtSEEK structured issues

**Read `docs/ISSUE-CONVENTIONS.md` first** — it defines the type enum, area
labels, the 7-section description schema, epistemic tags, and safety rules.
This skill only holds the workflow and the hard gates.

## Hard rules

1. **NEVER perform any write to GitHub — creating, commenting on, editing,
   labeling, or closing an issue, or creating/editing labels — without
   explicit human approval in this conversation.** The repo is PUBLIC and
   issues are outward-facing. Read-only `gh issue list/view` and `gh label
   list` are always fine.
2. **Never include secrets or credential values** in a draft. The validator
   scans, but you are the first line.
3. **Root cause is never guessed.** If it is not established with evidence,
   use the sentinel line the conventions define.

## Workflow

1. **Draft** the issue to a local scratch file: frontmatter + the seven
   sections exactly per the conventions doc's Description schema and worked
   example.
2. **Validate** until clean:
   `uv run python scripts/validate_issue.py <draft.md> --labels`
   (If bare `uv run` cannot reach the repo env, use the repo-mounted
   container form in `docs/ISSUE-CONVENTIONS.md` § Filing an issue.)
3. **Duplicate-check:** `gh issue list --state all --search "<key behavior
   words>"`. If a matching issue exists, propose commenting/relabeling it
   instead of filing a duplicate.
4. **Present the full draft** (title, labels, body) to the user and ask
   whether to file it. Any label that would be newly created must be
   explicitly called out in the presented draft ("this will mint new label
   `area: X`"). Do not proceed on silence.
5. **On approval:** ensure the labels exist (`gh label list`); if a new
   `area:` label is needed, follow the minting rule in
   `docs/ISSUE-CONVENTIONS.md` § Area labels — the same change must update
   the doc's table and `scripts/seed_issue_labels.sh`. Then:
   `gh issue create --title "<title>"
   --label "<label1>" --label "<label2>" --body-file <draft-body.md>`
   (body = the draft minus its frontmatter). Report the issue URL.

## When you find deferred work mid-task

Do not wait for the user to ask. Say what you found, why it is out of scope
right now, and offer to draft the issue — then follow the workflow above.
