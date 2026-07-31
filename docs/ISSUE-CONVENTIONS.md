# NExtSEEK GitHub issue conventions

NExtSEEK tracks deferred work, bugs, and design decisions as GitHub issues on
this repo. To keep that surface searchable and durable across contributors
(human or agent), every issue carries a closed `type:` label, at least one
`area:` label, and a structured seven-section body.

**`scripts/validate_issue.py` is the single source of truth.** The type enum,
the area name rule, the seeded area list, the section schema, and the
root-cause sentinel all live there as code — this document, the GitHub Issue
Form (`.github/ISSUE_TEMPLATE/structured-issue.yml`), and the label seeder
(`scripts/seed_issue_labels.sh`) are all drift-guarded against it (see
`nextseek_api/cc_assistant/tests/test_issue_conventions_guard.py`). If this
doc and the validator ever disagree, the validator wins — file a
`type: docs` issue against this file.

**This repo is PUBLIC.** Every issue body is world-readable the moment it's
filed. Two hard rules follow from that, for humans and agents alike:

- **Validate before filing.** Run `scripts/validate_issue.py` on the draft
  and fix every error — including its secret-pattern scan — before creating
  the issue.
- **Ask a human before filing.** Filing a GitHub issue is an outward-facing
  action. An agent that identifies a deferred bug, a plan residual, or
  follow-up work must draft the issue and present it for approval — never
  file it unattended. See `## Filing an issue` below.

## Type labels

Exactly **one** `type:` label per issue (validator-enforced — the field is a
closed enum, not a set).

| Label | Definition |
|---|---|
| `type: bug` | Existing functionality behaves incorrectly against its intended behavior |
| `type: enhancement` | New capability, or an improvement to correct behavior |
| `type: task` | Neither bug nor feature: refactor, port, migration, test debt, process |
| `type: docs` | Documentation-only (in-repo docs, docstrings, OpenAPI examples) |
| `type: performance` | Correct but too slow or resource-hungry |
| `type: security` | Injection surface, secrets exposure, authz gap — regardless of proven exploitability |
| `type: data-hygiene` | Bad/missing/orphaned data in live stores; the code may be fine |
| `type: design-question` | A maintainer decision is needed before code; closing = recording the ruling |
| `type: ops` | Deployment, installation, infrastructure, or operational degradation |

**Disambiguation:**

- Performance problem caused by a bug → `bug`, not `performance`.
  `performance` is for code that is correct but inherently too slow or
  resource-hungry.
- Anything security-relevant wins → `security`, even if it would otherwise
  read as a `bug` or `task`.
- Data damage caused by a code defect is **two** issues: `bug` for the
  defect itself, `data-hygiene` for the cleanup of the data it already
  damaged.

`priority: low|medium|high` are the existing priority labels — optional,
unchanged, and orthogonal to `type:` (an issue can have any type at any
priority). The optional bare `needs-ruling` label marks an issue that is
blocked on a maintainer decision; it commonly pairs with
`type: design-question` but can be added to any type whose next step is a
ruling rather than code.

## Area labels

At least **one** `area: <name>` label per issue (validator-enforced count —
zero areas is rejected). Unlike `type:`, the area set is **open**: new areas
can be minted as the codebase grows.

**Name rule:** lowercase, `-` or `_` separators, regex
`^[a-z0-9]+([_-][a-z0-9]+)*$`. Underscores are reserved for areas that mirror
an on-disk code path (`cc_assistant`, `chat_nextseek`, `nextseek_api`);
everything else uses hyphens.

**Minting rule:** before adding a new area, check `gh label list` for a
near-match. If none exists, minting one touches all three of these
touch-points in the same commit/session: (1) add its one-line "covers" entry
to the table below, (2) add a matching `create "area: <name>" ...` line to
`scripts/seed_issue_labels.sh`, and (3) actually run `gh label create` (or
re-run the seeder) so the label exists on GitHub. The drift guard
(`nextseek_api/cc_assistant/tests/test_issue_conventions_guard.py::TestSeedScript::test_area_labels_match_seeded`)
diffs this table against the seeder script and goes red until they agree —
the area list here, the seeder, and the labels actually in use must never
drift apart.

**Seeded starter set (15):**

| Label | Covers |
|---|---|
| `area: cc_assistant` | nextseek_api/cc_assistant/, docker/cc-runtime/, agent plugin+skills |
| `area: chat_nextseek` | NS pipeline: router/entity/parser/api agents, prompts, catalogs |
| `area: nextseek_api` | REST layer not covered by a narrower area |
| `area: seek-proxy` | SEEK passthrough ViewSet family |
| `area: ui` | chat frontend, embedded bundle, templates |
| `area: upload` | classic sample/datafile upload |
| `area: batch-upload` | `_batch_upload_*.py`, bins, skill |
| `area: sample-search` | attribute/keyword-based sample search: find, list, or filter samples by type, treatment, keyword, or assay |
| `area: project-search` | project- and investigation-level search and lookup queries |
| `area: router` | BAML route decision, route_capabilities, history context |
| `area: schema-rag` | schema-aware retrieval that grounds NL queries against the live API's OpenAPI schema (`nextseek_api/schema_rag/`) |
| `area: search-solr` | SEEK's Solr search index integration |
| `area: graph-neo4j` | the sample/assay relationship graph: sync, schema, and queries |
| `area: deployment` | compose topology, images, env/config delivery |
| `area: installer` | startup.sh / startup/ install, reset, doctor, seeding |
| `area: seek` | SEEK's own Rails-side codebase and behavior — distinct from `seek-proxy` (this repo's passthrough ViewSet layer) and `search-solr` (SEEK's Solr integration) |

## Description schema

**Title:** behavior-first, 15–120 characters — "X does Y when Z", never an
action ("Fix X"). The validator rejects titles starting with
`fix`/`fixes`/`fixed`/`implement`/`implements`/`todo`.

**Draft format:** one file per issue — a YAML frontmatter block
(`title`, `type`, `areas`, `priority?`, `needs_ruling?`) followed by the
markdown body. The frontmatter becomes the `gh issue create` flags
(`--label`); the body becomes `--body-file` as-is.

**Seven body sections, in order.** Agent drafts use `##` headings; the
GitHub Issue Form renders `###`; the validator accepts either:

| # | Section | Required? | Contract |
|---|---|---|---|
| 1 | Summary | yes | 1–3 sentences, observed behavior or need, plain language |
| 2 | Evidence | yes | file:line at a stated commit, commands + outputs, logs. Every claim carries one epistemic tag: `test-proven` \| `reproduced-live` \| `code-reading` \| `inference`. Validator: ≥1 tag AND ≥1 file:line/command/commit ref |
| 3 | Impact | yes | What breaks, for whom, how often; severity rationale |
| 4 | Root cause | heading always present | Real analysis OR the exact sentinel `Not established — do not guess.` Never empty, never speculation (enforces the house symptom-vs-root-cause rule) |
| 5 | Suggested fix direction | optional | Known candidates, prior art (commit refs), explicitly non-binding |
| 6 | Verification recipe | yes | How a future implementer confirms it exists now AND confirms it fixed later (commands, test lane) |
| 7 | Provenance | yes | Origin (plan/report/outstanding-items id, date), related issues/PRs |

The four epistemic tags — `test-proven`, `reproduced-live`, `code-reading`,
`inference` — are the only vocabulary the Evidence section accepts; every
bullet in Evidence must carry exactly one, inline, as shown in the worked
example below.

The Root cause section's heading is never omitted. When a real root cause
has not been established, write the sentinel exactly as it appears here,
character for character:

```
Not established — do not guess.
```

The sentinel may be followed by additional non-causal context (e.g. what was
ruled out), as long as the sentinel line itself is character-exact.

An empty Root cause section fails validation. Writing a guess instead of the
sentinel does not fail validation — the validator cannot tell a guess from
real analysis — but it violates the convention and fails review.

## Filing an issue

**Human path (web UI):** use the Issue Form at issue-creation time on
GitHub — it walks through the `type:` dropdown (9 values), a required
`areas` text input (placeholder text lists the seeded examples and the
kebab/underscore rule), and required textareas for Summary, Evidence,
Impact, Verification recipe, and Provenance, with Root cause pre-filled with
the sentinel. GitHub Issue Forms cannot map dropdown or textarea answers to
labels automatically, so **web-filed issues get their `type:`/`area:`
labels applied at triage** by a maintainer, not at creation time.

**Agent path (any tool):**

1. Draft the issue to a local file: YAML frontmatter + the seven sections
   above (see `## Description schema`).
2. Validate it: `uv run python scripts/validate_issue.py draft.md --labels`
   on a synced checkout, or the repo-mounted container form on the dev box,
   e.g.:

   ```
   docker run --rm --network none \
     -v /path/to/NExtSEEK:/repo -w /repo nextseek-nextseek:latest \
     uv run --project /app --no-sync python scripts/validate_issue.py \
     /repo/path/to/draft.md --labels
   ```

   Fix every reported error — including the secret-pattern scan — until the
   command exits 0.
3. Duplicate-check before proposing to file: `gh issue list --search
   "<key terms>"` against open and recently-closed issues. If a match
   exists, propose relabeling or commenting on the existing issue instead
   of filing a duplicate.
4. Present the full draft and its computed labels to the human. **Never
   file without explicit human approval** — filing is an outward-facing,
   public-repo action.
5. On approval, file it with the validated title, labels, and body:
   `gh issue create --title "<title>" --label "type: <type>" --label
   "area: <area>" [--label "priority: <priority>"] [--label
   "needs-ruling"] --body-file draft-body.md`, then report the issue URL
   back to the human.

## Label seeding

`scripts/seed_issue_labels.sh [owner/repo]` idempotently creates the 9
`type:` labels, the 15 seeded `area:` labels, and the bare `needs-ruling`
label (it leaves the existing `priority:*` labels untouched). It is
maintainer-run — re-run it after adding a newly minted area to the table
above, or after cloning the label set into a new repo.

## Worked example

The following draft is validator-clean end to end — parsing, all field
constraints, the evidence/epistemic-tag contract, and the secret scan. Use
it as the reference shape for a new draft.

```markdown
---
title: "ChatSession order_by('-updated_at') sites fetch multi-MB results_history and can hit MySQL 1038 'Out of sort memory'"
type: bug
areas: [cc_assistant, nextseek_api]
priority: medium
---

## Summary
Three call sites order the user's full ChatSession set with every column selected,
including the multi-MB `results_history` JSONField. Under MySQL's rowid-sort strategy
this raises errno 1038 and kills the request; an identically-shaped site already failed
live and was fixed — these three were not.

## Evidence
- reproduced-live (sibling site): OperationalError 1038 killed a live cc-assistant turn
  (2026-07-22); traceback landed on `services/cc_assistant.py:140`.
- code-reading: unprotected sites `services/cc_assistant.py:201` (_resolve_session, hot
  path), `services/assistant.py:694`, `services/assistant.py:821` — all
  `.filter(user=...).order_by("-updated_at").first()`, no defer/two-step.
- reproduced-live: `results_history` measured MAX 45,418,638 bytes, AVG ~619 KB over
  109 sessions for one user; `@@sort_buffer_size` = 262144.
- inference: intermittent — a 2026-07-23 read-only probe ran all three shapes without
  error; trigger depends on MySQL's packed-addon vs rowid filesort choice.

## Impact
Any chat user with a large session history can have requests fail mid-turn (500)
nondeterministically; `_resolve_session` is on the default path of every cc-assistant
query that lacks an explicit session_id.

## Root cause
Full-row ORDER BY over multi-MB JSON columns exceeds sort_buffer_size under rowid
sort. Established via the fixed sibling (`services/assistant.py:439-446`, whose comment
documents the same failure) and the live 1038 traceback.

## Suggested fix direction
Non-binding: apply the existing two-step PK-lookup pattern (assistant.py:439-446) or
`.defer("results_history")` — but `.first()` results are returned to callers, so check
each caller for later `results_history` access before deferring.

## Verification recipe
Confirm-present: `grep -n 'order_by("-updated_at")' nextseek_api/services/{assistant,cc_assistant}.py`
→ three cited sites select all columns. Confirm-fixed: same grep shows defer/two-step at
all three; `pytest nextseek_api/cc_assistant/tests/` green.

## Provenance
outstanding-items id `chatsession-orderby-1038-sibling-sites` (2026-07-23); plan-010 D2
log test_03-1784775995.log:665; related fix commit 2f942f2 (_session_metas defer).
```
