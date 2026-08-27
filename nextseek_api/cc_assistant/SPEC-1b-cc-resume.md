# Spec: Step 1b — Container-CC `--resume` (core multi-turn context)

**Date:** 2026-06-29
**Tracker:** `integration-plan.json` step **1b** (Step 1 "Fix correctness blockers")
**Status:** design, awaiting user review → writing-plans
**Scope note:** This spec is **core resume only**. The cross-session learning layer
(distilled per-user memory + raw-on-demand transcripts + `fresh_session` flag) is
**deferred to tracker substep 1c** and gets its own spec. See "Out of scope" below.

---

## 1. Problem

A Container-CC (CC) chat does **not** continue context across turns. Each CC turn
runs a **fresh, destroyed-after** container (`containers.run(...)` →
`container.remove(force=True)`, `cc_engine.py:445,545`); HOME is `/home/user` (image
default, no override); only `/data/scratch` (rw) and `/data/projects` (ro) are
mounted. Claude Code writes its session transcript to
`~/.claude/projects/<cwd-hash>/<session-id>.jsonl`, which therefore **dies with the
container**. So a turn-2 `claude --resume <id>` would find no transcript and start
blank.

The resume *infrastructure already exists* but is unwired:
- `_build_command(...)` appends `--resume <session_id>` when given one (`cc_engine.py:308-309`).
- `run_cc_turn(..., session_id=...)` accepts the param (`cc_engine.py:369`).
- The translator already surfaces Claude's in-container session UUID as
  `cc_session_id` on terminal `query_complete`/`query_error` frames (the Step-1a
  groundwork: `translate.py:75-79,142-156`).
- `ChatSession.extra_state` (a `JSONField`) exists as durable per-session storage
  (`assistant/models_db.py:16`).

What's missing: (a) the service layer always calls `run_cc_turn(session_id=None)`
(`services/cc_assistant.py:176`); (b) nothing persists the returned `cc_session_id`;
(c) nothing persists the **transcript** across the ephemeral containers.

## 2. Goal / success criteria

A user holding a CC chat can ask a follow-up that depends on the prior turn and CC
answers with full context. Concretely, the live test: turn 1 establishes a fact
("my name is X" / "remember the value 42"); turn 2 recalls it correctly. No cross-user
or cross-chat leakage. No regression to the NS route or to OI-3 isolation.

## 3. Design

Two pieces of state must survive between containers: **the resume id** (which session
to continue) and **the transcript on disk** (what `--resume` reads).

### 3.1 Resume id — `ChatSession.extra_state["cc_session_id"]`

- **Read (before the turn):** in `_start_task`, read
  `chat_session.extra_state.get("cc_session_id")` and pass it as
  `run_cc_turn(session_id=...)`. Turn 1 (key absent) → `None` → no `--resume` → fresh
  session.
- **Capture (after the turn):** wrap `send_event` in the **CC branch only** with a
  small sniffer that watches event data for `cc_session_id` and persists it via a
  **single-key read-modify-write**:
  `chat_session.extra_state["cc_session_id"] = <id>` then
  `chat_session.save(update_fields=["extra_state", "updated_at"])`. Re-captured every
  turn, so it's robust if Claude rotates the id under `-p --resume`.
- **Rejected alternatives:** editing the shared `make_db_event_callback` (entangles
  the NS route — risky); changing `run_cc_turn`'s return type (the value already flows
  through events).

### 3.2 Transcript — per-session persistent `.claude` mount

New RW bind mount whose **source path encodes both identities**:

```
<host_cc_state_root>/<user_id>/<chat_session_id>  →  /home/user/.claude   (rw)
```

- New `CCPaths` field `host_cc_state_root` (env `DMAC_HOST_CC_STATE_ROOT`, default
  beside the scratch root). New `run_cc_turn` param `cc_state_key` (the
  `chat_session_id`) used **only** to build this mount path — kept distinct from
  `session_id` (the resume value).
- The whole `.claude` is mounted (not just `projects/`) so Claude can freely write its
  own state (settings, history, `projects/<hash>/*.jsonl`) without hitting a
  root-owned auto-created parent dir. This keeps the door open for 1c to layer a
  per-user memory file as a nested read-only bind at `~/.claude/CLAUDE.md` without
  disturbing resume mechanics.
- `cc_state_key` runs through the **same single-segment path-traversal guard** already
  applied to `user_id` (`cc_engine.py:140`); the host dir is created with the same
  ownership/permission handling used for scratch.

### 3.3 The baked `CLAUDE.md` is left untouched

Verified: `/app/CLAUDE.md` (root-owned, 7.7 KB) is symlinked from
`/home/user/CLAUDE.md` and loaded as the **project/cwd-tier** memory; it carries
load-bearing rules (credential safety, "never call AskUserQuestion", router behavior,
stop-after-2). Spec 1 does **not** touch it. The per-session `.claude` mount only
affects `~/.claude/...`, which the baked doc itself names as the per-turn state
location — fully consistent. (The doc's "state persists across turns" line assumes the
legacy long-lived container; NExtSEEK's ephemeral model is precisely why the mount is
needed. The mount *restores* that invariant.)

## 4. Isolation (defense-in-depth — the explicit requirement)

The hard boundary is **cross-user**; it must hold absolutely.

1. **App layer (already true):** `_resolve_session` does
   `ChatSession.objects.get(session_id=…, user=request.user)` — a user can only load
   their own session, hence only their own `cc_session_id`. Cross-user resume is
   impossible before the engine runs.
2. **Mount layer (new):** the bind *source* is `<user_id>/<chat_session_id>`. A
   container sees only that one session's transcripts. A leaked/guessed Claude UUID is
   useless in another user's container — the file isn't mounted there.
3. **Path safety:** `user_id` (`request.user.username`) and `cc_state_key`
   (`chat_session_id`, a UUID) are both single-segment path-guarded.
4. **No artifact leak:** the post-turn publisher only snapshots `/data/scratch`; the
   `.claude` mount is never published to user-downloadable output.

OI-3 preserved: no credentials are written into `.claude` (creds are injected via env
and never persisted there); the mount is the agent's own config/transcript dir.

## 5. Edge cases

- **Id present but transcript missing** (a session predating 1b, or store eviction):
  `claude --resume <missing>` would error. Handling: if `extra_state` has no
  `cc_session_id` → fresh (turn-1 path); if present → attempt `--resume`; on a
  resume-not-found failure from the engine → fall back to **one** fresh run within the
  same turn (logged, capped so it can't loop). Legacy sessions simply have no stored id
  and take the fresh path — no special-casing needed.
- **Id rotation under `-p --resume`:** re-capturing `cc_session_id` every turn handles
  it. (Empirical: confirm during implementation how many `.jsonl` files this Claude
  Code version produces per turn — append vs fork — and that we store the right id.)
- **Concurrency:** per-`chat_session_id` store means two turns of the *same* chat could
  in principle race on the same dir; in practice turns within one chat are sequential
  (HTTP request → task). Cross-chat turns use different dirs (no race).

## 6. Testing (TDD-first)

Hermetic units (the box can't run the Django test-DB runner — `seek_db_user` lacks
`CREATE`; use the `uv run --no-project --with pytest ... --noconftest` pattern as in 1a):

- **Service:** stored id → `run_cc_turn` receives `session_id=<id>` (mock the engine,
  assert kwarg). RED first.
- **Service:** no stored id → `session_id=None`.
- **Service:** terminal event carrying `cc_session_id` → persisted to `extra_state` +
  `save(update_fields=...)`; other `extra_state` keys untouched.
- **Engine:** `run_cc_turn` builds the `<host_cc_state_root>/<user>/<key>` →
  `/home/user/.claude` mount; the path guard rejects a malicious `cc_state_key`.
- **Engine:** `test_command_resume_appended_last` already covers the flag.
- **Isolation:** two users/sessions → distinct mount sources; cross-user
  `_resolve_session` → `DoesNotExist`/404.

Live verification (forced-CC harness, as in 1a, ~1 Opus turn ≤ $2 cap): two-turn
conversation establishing then recalling a fact; assert the transcript persisted in the
per-session host dir and `--resume <id>` was passed on turn 2.

## 7. Deployment

Same Step-0 procedure under per-change sign-off: snapshot `:pre-step1b` → fast-forward
the service-account build-context clone → rebuild → recreate (`--no-deps nextseek` via
the SA `docker:cli` helper). New operational requirement: the host `host_cc_state_root`
dir must exist and be mountable (mirrors `host_scratch_root`); set the default + document
`DMAC_HOST_CC_STATE_ROOT`.

## 8. Out of scope (→ tracker 1c, separate spec)

- Distilled per-user rolling memory (`~/.claude/CLAUDE.md` user-tier, composed with the
  baked project file via Claude's native memory hierarchy).
- 10-most-recent raw transcripts mounted read-only for on-demand depth.
- The per-chat `fresh_session` flag.
- Any post-session summarization step.

These depend on 1b's persistence foundation but are enhancements, not correctness
blockers, and carry more surface area (summarization cost, memory injection). 1b ships
and verifies independently first.

## 9. Files (anticipated)

- `nextseek_api/services/cc_assistant.py` — read id before turn; `send_event` sniffer +
  persist after; pass `cc_state_key`.
- `nextseek_api/cc_assistant/cc_engine.py` — new `.claude` mount + `cc_state_key` param
  + path guard + host-dir creation.
- `nextseek_api/cc_assistant/cc_config.py` — `host_cc_state_root` / `DMAC_HOST_CC_STATE_ROOT`.
- Tests under `nextseek_api/cc_assistant/tests/` (+ a service-layer test module).
