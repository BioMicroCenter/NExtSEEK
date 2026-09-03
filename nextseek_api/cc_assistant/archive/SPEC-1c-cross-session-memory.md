# Spec: Step 1c — Cross-session memory (distilled per-user memory + raw-on-demand + `fresh_session`)

**Date:** 2026-06-29
**Tracker:** `integration-plan.json` step **1c** (Step 1 "Fix correctness blockers")
**Status:** design, awaiting user review → writing-plans
**Builds on:** Step **1b** (per-session `.claude` persistence + `--resume`). 1c layers a
learning/memory tier on top of 1b's foundation; it does not change resume mechanics.

---

## 1. Problem

After 1b, a Container-CC (CC) chat continues context **within one chat session** (the
per-session `.claude` mount + `--resume`). But the agent has **no memory across
sessions**: starting a new chat begins from zero, and a chat the user returns to is
blind to work done in *other* sessions since. Each session is an island.

We want a per-user **rolling memory** — a short, durable distillation of what the user
has done across their recent sessions — auto-loaded into every CC turn, plus the ability
to **drill into the raw transcripts** of recent sessions on demand, plus an escape hatch
to run a **clean-room** chat with no memory.

The key conceptual split that the whole design turns on:

> **An individual session summary is only ever about its own session.** Cross-session
> awareness is assembled *at render time* from the *other* sessions' summaries. A
> session never needs a summary of itself — it already has its full transcript via 1b's
> `--resume`.

## 2. Goal / success criteria

- A user who did work in session A, then B, then C, and **returns to continue A**, finds
  that A's continuation is aware of B and C (via the injected memory) — while A's own
  detail comes from `--resume`, not from a redundant self-summary.
- Memory is **grounded**: every remembered item cites transcript lines, and quotes are
  verified against those lines.
- **Strict per-user isolation**: a user's memory and raw transcripts are built only from
  their own sessions; no cross-user leakage. **OI-3 preserved**: the summarizer runs
  host-side; the agent stays zero-creds; memory + transcripts mount **read-only**.
- A `fresh_session` chat skips the memory layer entirely.
- No regression to the NS route, to 1b resume, or to OI-3.

## 3. Architecture overview

Two layers, one LLM call each *per session* (not per turn):

1. **Distillation (LLM, host-side):** when a session leaves the foreground, a host-side
   **BAML `Summarize()`** function turns that session's transcript `.jsonl` into a
   validated **`SessionSummary`** (pydantic), stored in `ChatSession.extra_state`.
2. **Rendering (deterministic, no LLM):** before a CC turn, a plain-Python renderer
   merges the user's **10 most-recent *other*** session summaries into a per-user
   `~/.claude/CLAUDE.md` and mounts it (and the 10 raw transcripts) **read-only** into
   the agent, composing with the baked project `CLAUDE.md`.

The only recurring LLM cost is layer 1, once per changed session, on a **cheap Gemini
client** — fully off the Opus-only bedrock-proxy.

## 4. The summary — `SessionSummary` schema

BAML emits this as a **pydantic v2** model (`output_type "python/pydantic"`), so "the
validated structured object" is native. Lighter than a `/handoff` report: a gist plus a
capped, grounded item list.

```
EvidenceRef:
  line_start : int    # 1-based line index into the source transcript .jsonl
  line_end   : int    # >= line_start
  quote      : str    # short verbatim excerpt expected at [line_start, line_end]
  verified   : bool   # set host-side: True iff `quote` is a substring of those lines

MemoryItem:
  category   : "preference" | "context" | "artifact" | "decision" | "todo" | "fact"
  statement  : str            # ONE concise sentence — the durable takeaway
  evidence   : EvidenceRef[]  # >= 1
  confidence : "high" | "medium" | "low"

SessionSummary:
  # provenance of the source
  chat_session_id       : str
  claude_session_id     : str | null
  transcript_path       : str
  transcript_line_count : int
  turn_count            : int
  chat_model            : str         # e.g. us.anthropic.claude-opus-4-8
  # the distillate
  gist   : str                        # 1-line summary of the whole session
  items  : MemoryItem[]               # capped at <= 8
  # provenance of the summary itself
  writer        : "baml_gemini" | "fallback_actions"
  summary_model : str                 # the Gemini model id used (or "none" for fallback)
  schema_version: str
  generated_at  : str
```

- **BAML-side integrity:** `quote @check(nonempty)`, `line_start @assert(>= 1)`,
  class-level `@@assert(line_end >= line_start)`. `@check` keeps the item but records the
  failure; `@assert` drops a malformed nested item. (See BAML checks-and-asserts.)
- **Host-side grounding:** `verified` is set by us — we check `quote` is a substring of
  the cited line range in the original `.jsonl`. **Unverified items are kept and flagged**
  (`verified=false`), and the renderer marks them visually; they are not silently dropped.

## 5. Write path — producing a summary

### 5.1 Trigger (stateless, per-session)

HTTP is stateless, so there is no explicit "switched away from X" signal. Instead, **on a
turn for session `Y`**, in `_start_task` *before* mounting Y's memory:

1. Query the user's sessions `order_by("-updated_at")`, **excluding Y**.
2. Take the **single most-recent** one whose transcript **fingerprint changed** since its
   last summary (`line_count` + content hash stored in `extra_state`), and
   **summarize it synchronously** (≈1 Gemini call of first-turn latency — bounded).
3. Everything else stale is left to the **Celery sweep** (§5.4).

This naturally handles "ongoing vs returned-to": the *active* session is always `Y` and
is never summarized while active; a returned-to session is summarized only once the user
moves on from it and its fingerprint changed. We never need to distinguish them by intent.

### 5.2 Input preparation (latency-first parsing)

The transcript `.jsonl` (the session's file under `<cc_state_root>/<user>/<session>/
projects/**/*.jsonl`; the most-recently-modified if more than one) is parsed in a single
pass:

- **`orjson`** parses each line (Rust-backed); lines are kept **1-based** and the raw line
  bytes retained for evidence substring-verification.
- A **`pydantic.TypeAdapter`** over a **discriminated union** (`Field(discriminator=...)`,
  `extra="ignore"`) bulk-validates only the record types we consume (user/assistant text,
  `tool_use`, `tool_result`); unknown record types are skipped (resilient to Claude Code
  transcript-format drift).
- The summarizer is fed an **actions view** — exact `Bash` commands; `Write`/`Edit`/
  `MultiEdit`/`NotebookEdit` file paths + op (created/edited/deleted); `Read` paths;
  Skill/plugin invocations — **plus** text turns and **truncated** tool outputs (first
  ~500 chars, env-configurable). Lines are pre-numbered so the model cites `L<n>`.

### 5.3 Summarizer (host-side BAML / Gemini)

- A new BAML `function Summarize(input: SummarizeInput) -> SessionSummary` on a **new
  cheap flash-tier Gemini `client<llm>`** (its own `clients.baml` block; `GCP_API_KEY`,
  already in the process env for the router). Mirrors the router's call discipline:
  **lazy guarded import** of the generated client, **`asyncio.run(...)`** bridge from the
  sync task thread, a **Python-side fallback**, and a frozen result with a `writer` field.
- **Failure → deterministic actions-only fallback** (`writer="fallback_actions"`): build a
  minimal `SessionSummary` directly from the parsed actions view (commands/files/skills),
  each item grounded on the exact `tool_use` lines. No LLM, still grounded — so a Gemini
  outage never leaves a session un-remembered. (Same spirit as the router heuristic.)
- **Persist:** `extra_state["summary"] = <SessionSummary dict>`,
  `extra_state["summary_fingerprint"] = {line_count, hash}`, via single-key
  read-modify-write + `save(update_fields=["extra_state", "updated_at"])` — the 1b pattern.

### 5.4 Celery sweep (backstop)

A periodic task (the deployment already runs a Celery worker) scans for sessions **idle >
15 min** (env-configurable) whose fingerprint changed since last summary and summarizes
them. This catches sessions the user never "left" via a new chat, and retries
fallback/failed ones. Idempotent: a session whose fingerprint is unchanged is skipped.

## 6. Read path — assembling + mounting memory

### 6.1 Deterministic renderer (no LLM)

For a turn on session `Y` (unless `fresh_session`):

1. Pull the user's **10 most-recent sessions by `updated_at`, excluding `Y`** (so Y never
   gets a redundant/stale self-summary; A returning to the foreground correctly sees B/C).
2. Read each **available** `SessionSummary` from `extra_state` (sessions not yet
   summarized are skipped — the sweep fills them in); **group items by category** and
   order within each by **confidence + recency**; mark `verified=false` items. The
   10-session window **is** the size cap — there is no separate item/byte budget.
3. Render a markdown **per-user `CLAUDE.md`** on host (atomic tmp+rename; last-writer-wins
   for this derived per-user file).
4. Append a **pointer block** listing the 10 raw transcripts that will be mounted
   (session id, date, one-line gist from each summary) + a one-line how-to-read.

### 6.2 Mounts

- **Nested RO bind** of the rendered file at `/home/user/.claude/CLAUDE.md`, layered over
  1b's per-session `.claude` RW mount (a more-specific bind on top of the dir bind).
- **RO bind** of the 10 most-recent *other* sessions' transcripts (the "A" half of B+A —
  on-demand depth), into a dedicated read-only path the pointer block names.
- **`fresh_session=true`** (new `QueryRequest` field) → **skip both** memory mounts for
  that turn. API field wired now; user-facing UI toggle deferred to the Step-3 UI work.

### 6.3 Composition with the baked `CLAUDE.md`

The rendered file is the **user-tier** memory (`~/.claude/CLAUDE.md`); the baked
`/home/user/CLAUDE.md` (1b §3.3) is the **project/cwd-tier** memory. Claude Code's native
hierarchy loads **both**. **The baked file is never edited.**

> **Impl task 1 (gating) — VERIFIED 2026-06-29 (live-confirmed):** probe at
> [`nextseek_api/cc_assistant/evidence/1c-claude-md-merge-probe.md`](nextseek_api/cc_assistant/evidence/1c-claude-md-merge-probe.md).
> Zero-spend mount topology + production entrypoint checks; **live forced-CC turn
> confirmed both `Write-safety on NExtSEEK` (project tier) and
> `USER_MEMORY_MARKER=1C_PROBE_ALPHA` (user tier) in loaded instructions (~$0.14 Opus).
> Verdict: **MERGE — keep the planned nested RO bind injection point.**

## 7. Storage

All per-session state rides the existing **`ChatSession.extra_state`** `JSONField`
(no new table), keyed naturally per session and queryable host-side by the renderer:

- `extra_state["cc_session_id"]` — from 1b (unchanged).
- `extra_state["summary"]` — the `SessionSummary` dict.
- `extra_state["summary_fingerprint"]` — `{line_count, hash}` for change detection.

Each session writes only its **own** key set (no cross-session write contention). The only
shared artifact is the rendered per-user `CLAUDE.md` file, written atomically.

## 8. Isolation (defense-in-depth) & OI-3

The hard boundary is **cross-user**; it must hold absolutely.

1. **App layer (already true):** every session read is
   `ChatSession.objects.filter(user=request.user)` / `get(..., user=request.user)` — a
   user's renderer and summarizer only ever see their own sessions.
2. **Mount layer:** the per-user `CLAUDE.md` and every raw-transcript bind *source* is
   keyed by `<user_id>` (and `<chat_session_id>`), all run through 1b's single-segment
   `_validate_user_id` guard before interpolation. Both new mounts are **read-only** — the
   agent cannot mutate memory or another session's transcript.
3. **OI-3:** the summarization LLM call runs **host-side** in the trusted Django process
   (which already holds `GCP_API_KEY` for routing) — **not** in the de-credentialed agent.
   No new credential ever enters the sandbox; the agent's memory/transcript mounts are RO.
4. **Data path:** the transcript text already goes to Gemini for per-turn routing today, so
   summarization introduces **no new external data path** — noted for prod governance.

## 9. Edge cases

- **Cold start / no history:** turn 1, or a user with no other sessions → renderer produces
  nothing → memory layer simply absent; the baked project `CLAUDE.md` still composes in.
- **Returned-to session with newer siblings:** resolved by §6.1 (exclude own summary;
  window ordered by `updated_at`) — A's continuation sees B and C; A's own detail is from
  `--resume`.
- **Unverified evidence:** kept and flagged, never silently dropped (§4).
- **Multiple `.jsonl` per session:** use the most-recently-modified (1b showed one file per
  session in practice).
- **Concurrency on the rendered file:** atomic tmp+rename, last-writer-wins (derived,
  regenerable).
- **Summarizer outage:** deterministic actions-only fallback (§5.3) — never blocks chat.
- **Large transcript:** bounded by truncation (§5.2); the LLM call dominates wall-clock, so
  `orjson` + bulk `TypeAdapter` keep the parse cheap, which matters most for the sweep.

## 10. Testing (TDD-first)

Hermetic units (the box can't run the Django test-DB runner — `seek_db_user` lacks
`CREATE`; use the `uv run --no-project --with pytest ... --noconftest` pattern as in 1a/1b),
all isolated in Django-free helpers where possible:

- **Parsing:** `.jsonl` bytes → actions view + numbered lines; `orjson`/`TypeAdapter` over a
  fixture transcript; unknown record types skipped.
- **Evidence verification:** a quote present at its line range → `verified=true`; absent →
  `verified=false` (kept).
- **Fingerprint:** unchanged transcript → no re-summarize; appended transcript → changed.
- **Selection:** given N sessions, the window is the 10 most-recent by `updated_at`
  **excluding `Y`**; the sync target is the single most-recent changed non-`Y` session.
- **Renderer:** summaries → grouped/capped markdown + pointer block; deterministic output;
  `fresh_session` → empty/no mount.
- **Fallback:** simulated BAML failure → actions-only `SessionSummary`, items grounded on
  `tool_use` lines, `writer="fallback_actions"`.
- **Isolation:** two users → distinct mount sources; a malicious `user_id`/key rejected;
  both new mounts are `mode: "ro"`.
- **BAML contract:** `Summarize` stubbed via a fake client (no network) returning a fixture,
  asserting the pydantic shape — mirrors the hermetic-suite constraint.

Live verification (forced-CC, ≤ $2 cap, plus a **UI Playwright** pass per the user's
standing preference): session A establishes a fact; start session B; return to A and ask
something only answerable from B's memory → A answers from the injected memory; confirm the
rendered `CLAUDE.md` + raw transcripts mounted RO and that `--resume` still works.

## 11. Deployment

- **BAML codegen discipline (as for the router):** add the `Summarize` function + Gemini
  client to `dmac-assistant/baml_src/`, run `make baml-generate`, then **re-vendor/commit**
  the regenerated `baml_client/` into the NExtSEEK clone so the Dockerfile `COPY` bakes it
  in (no runtime codegen).
- **Celery beat:** register the periodic sweep task; confirm the worker picks it up.
- **Env:** `GCP_API_KEY` (already present); new optional knobs for the summarizer model id,
  truncation budget, sweep idle threshold + cadence, and the memory window size (default 10).
- Same Step-0 procedure under **per-change sign-off**: snapshot `:pre-step1c` →
  fast-forward the SA build-context clone → rebuild → recreate (`--no-deps nextseek` via the
  SA `docker:cli` helper). Reuses 1b's `host_cc_state_root` bind; the prod host must carry
  the same binds/env (gitignored `nextseek.env` won't ride git).

## 12. Files (anticipated)

- `dmac-assistant/baml_src/` — new `Summarize` function + classes; new cheap Gemini
  `client<llm>`; regenerate → vendor `baml_client/` into the NExtSEEK clone.
- `nextseek_api/cc_assistant/cc_summary.py` *(new, Django-free)* — transcript parsing
  (`orjson` + `TypeAdapter`), actions view, evidence verification, fallback builder,
  fingerprint. Hermetically testable.
- `nextseek_api/cc_assistant/cc_memory.py` *(new, Django-free render core)* — group/rank/cap
  + markdown render + pointer block; the file-write/mount glue stays thin.
- `nextseek_api/cc_assistant/cc_engine.py` — two new RO mounts (`CLAUDE.md` nested bind +
  raw-transcript dir); `fresh_session` gate on mounting.
- `nextseek_api/cc_assistant/cc_config.py` — paths/knobs for the rendered-memory file, the
  raw-transcript mount, truncation, window size, sweep settings.
- `nextseek_api/services/cc_assistant.py` — stateless pre-turn trigger (select + sync
  summarize the one changed non-`Y` session), invoke renderer, pass mount params; persist
  summary + fingerprint to `extra_state`.
- `nextseek_api/assistant/models_api.py` — `fresh_session` field on `QueryRequest`.
- A Celery periodic **sweep** task (module per project convention).
- Tests under `nextseek_api/cc_assistant/tests/`.

## 13. Out of scope (1c)

- The user-facing `fresh_session` **UI toggle** (Step-3 UI work; API field ships here).
- Multi-user provisioning (tracker step 2) — 1c keeps the single demo user; the design is
  per-user-keyed and ready for it.
- Any second-pass LLM "re-distillation" of the rolling memory — the merge stays
  deterministic by decision.
