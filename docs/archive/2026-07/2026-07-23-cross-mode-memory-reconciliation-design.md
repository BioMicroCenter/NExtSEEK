# Cross-mode shared memory: reconcile the #8 branch with dev, one shared store, symmetric injection

Date: 2026-07-23
Issue: #9 (reframed from "retire ask_about_last_results" to a memory-subsystem reconciliation)
Scope: `nextseek_api/cc_assistant/`, `nextseek_api/services/cc_assistant.py`, `chat_nextseek/chat_memory.py`, the router BAML, and the CC-runtime nextseek ops.
Audience: this is also the coordination doc for Taisha, whose `dev` memory system is the keeper here.

## 1. Background and the decision this records

Two branches independently built conversation memory for the assistant so a follow-up
like "counts of sex and species of those monkeys" can resolve "those" to the previous
turn's 139 records:

- **`dev-v3-merge` (our #8 work):** `cc_history.py` builds a plain-text history block
  from the unified `chat_log` and (a) augments the router query with a soft steer that
  nudges follow-ups toward `container_cc`, (b) prefixes the CC turn's prompt via
  `cc_prompt_with_history`. Pragmatic prompt/string shim.
- **`origin/dev` (Taisha's spec-001 series):** a first-class `RouterInput.history` BAML
  field (typed `HistoryTurn[]`, explicitly context-only), an `NSTurnContext` digest
  (`ns_turn_context.py` + `ns_digest.py`) written into the CC agent's CLAUDE.md with a
  `nextseek-recall --turn N` affordance, and live `nextseek-recall` / `nextseek-query`
  ops so the CC agent pulls prior rows on demand from the same session store. More
  complete and cleaner.

**Double-memory verdict (Taisha's open question), with code evidence:** the concern is
real, but the overlapping pair is **our `cc_history` prompt-prefix vs her `ns_digest`
CLAUDE.md digest**, not `cc_history` vs `cc_memory`. `cc_history._turn_line` and
`ns_turn_context.from_bundle` / `ns_digest._render_turn` both project the same within-chat
`chat_log` facts (user_query + result count + sample UIDs), so a CC turn would receive the
same "turn 1 -> 139 monkeys" fact twice, and our copy is the weaker one (no `turn_id` /
`bundle_id`, no recall affordance). `cc_history` vs `cc_memory` is fine: within-session
chat vs cross-session memory, different layers, they compose. So the fix is to drop our
shim and adopt her system, not to hold her system back.

**The organizing principle (agreed):** there is ONE shared store, both modes write to it,
and both modes inject it, symmetrically. Concretely: the unified `chat_log` (plus the
`results_history` bundles that hold the rows), persisted per session in the database, is
the single source of truth. NS turns and CC turns both append to it; whichever mode
answers a turn reads the same store, projected through a lens that fits that mode.

## 2. Goals and non-goals

### Goals
1. Reconcile the two branches onto `dev-v3-merge` by adopting Taisha's `dev` memory/routing
   subsystem and dropping our now-redundant `cc_history` shim + soft steer, while keeping
   our unrelated service scaffolding (admin route-override + pipeline-active gate, the #4
   event-trace metadata, the #6 turn-timeout cap).
2. Establish and enforce the invariant: **one shared store, both modes write, both modes
   inject, symmetric across NS and CC.**
3. Close the one asymmetry that blocks that invariant: the CC-side digest currently
   surfaces prior NS turns but skips prior CC turns, so a CC turn after a CC turn cannot
   see the earlier CC result.

### Non-goals
- The original #9 items as written. "Nudge follow-ups to CC" is dropped (Taisha's history
  is deliberately context-only; a sample count belongs on the free deterministic NS path).
  "Retire `ask_about_last_results` / `refine_last_search`" is dropped: on the no-nudge
  design those NS modes are load-bearing (they resolve references for follow-ups that stay
  on NS). See section 7.
- Making CC-produced results recallable as row bundles (the way NS bundles are). CC turns
  render as context lines in the digest; giving them a `nextseek-recall`-style row store is
  a possible later extension, called out in section 5.4, not built here.
- The broader `dev` <-> `dev-v3-merge` branch reconciliation (the other ~11 non-memory
  `dev` commits: test/api realignments). Merging `origin/dev` pulls them in as a side
  effect; that is acceptable, but auditing them is out of this spec's scope. See section 9.
- Adding an NS fallback for CC failures. There is none today (CC unavailable / over-budget /
  timeout returns `query_error`); that is a pre-existing gap, tracked separately, not part
  of this reconciliation.

## 3. The reconciliation

Perform a real merge of `origin/dev` into `dev-v3-merge`, with a fixed conflict-resolution
policy. The plan will run a trial merge to enumerate the full conflict set; the
memory-subsystem policy is:

### 3.1 Drop from `dev-v3-merge` (our redundant shim)
- Delete `nextseek_api/cc_assistant/cc_history.py` in its entirety (superseded by
  `router_context.build_history` on the router side and `ns_turn_context` / `ns_digest` on
  the CC side).
- In `router.py`: drop our `_with_history` steer and the `decide(query, history: str)`
  signature. Take Taisha's `router.py` (typed `decide(query, history: list[HistoryTurn])`,
  `_load_router_deps` seam, real `reasoning`, `Unrelated` route).
- In `services/cc_assistant.py`: drop the `conversation_history =
  build_conversation_history(...)` line, the string `history=` threaded through
  `_decide_route`, and `query = cc_history.cc_prompt_with_history(...)` in the CC-turn call
  (revert to the raw `req.query`).

### 3.2 Take from `origin/dev` (pure adds, no conflict)
- `nextseek_api/cc_assistant/router_context.py` (`HistoryTurn`, `build_history`).
- `nextseek_api/cc_assistant/ns_turn_context.py` (`NSTurnContext`, `from_bundle`,
  `build_contexts`).
- `nextseek_api/cc_assistant/ns_digest.py` (`render_digest`, `compose_turn_claude_md`).
- The CC-runtime ops: `docker/cc-runtime/build_context/plugins/nextseek/bin/nextseek-recall`,
  `.../nextseek-query`, and the `_nextseek_runner.py` dispatch (`_dispatch_recall`,
  `_dispatch_query`).
- The router BAML: `dmac_assistant/baml_src/router.baml` (`RouterInput.history`,
  `HistoryTurn`) + regenerated `dmac_assistant/src/dmac_assistant/router/baml_client/` +
  the mirror at `docker/cc-runtime/baml_src/router.baml`, and `router/agent.py`
  (`_to_baml_history`, `route(..., history=...)`).

### 3.3 Take-hers (clean, no real conflict)
- `chat_nextseek/src/chat_nextseek/chat_memory.py`: our branch never touched it (it equals
  the merge-base), Taisha's version adds `router_choice` / `status` / `error` per turn and
  the answered-turn filter. Take hers wholesale.

### 3.4 Hand-merge (the one hard file): `services/cc_assistant.py`
Keep OUR scaffolding, splice in HER memory wiring:
- KEEP: `_decide_route` with the admin `force_route` gate and the
  `pipeline_agent.is_active(session)` NS-gate (commit 156db94); `_merge_extra_state`; the
  `cc_turn_meta` event-trace metadata (#4); `clamp_turn_timeout` / `max_turn_length_s`
  (#6).
- SWAP IN: build the router history with `router_context.build_history(chat_log)` and
  thread the typed `list[HistoryTurn]` through `_decide_route` -> `cc_router.decide`
  (replacing our string history); in the CC-memory block build
  `digest_md = render_digest(build_contexts(chat_log, results_history, session_id=...))`
  and `combined = compose_turn_claude_md(digest_md, memory_md)`, writing `combined` to the
  turn CLAUDE.md (replacing the memory-only `md`); restore `query = req.query` and add
  `chat_session_id = <cc_state_key>` to the CC-turn call (REQUIRED, or `nextseek-recall` /
  `nextseek-query` cannot resolve the session).

## 4. One shared store, both modes write

State the invariant explicitly and treat it as an acceptance criterion:

> For any turn T, whichever mode answers it reads, from the one shared session store
> (`chat_log` + `results_history`), a faithful summary of every prior ANSWERED turn,
> regardless of which mode produced that prior turn. And T, when it completes, writes its
> own answered-turn summary back to that same store.

Write side (already true, verify in the merge):
- NS turns append via `chat_memory.append_turn` (user_query, mode, result_summary, bundle
  cross-reference, `router_choice`, `status`).
- CC turns append via the CC turn-complete path (`cc_turn_complete`) with an int `turn_id`,
  `mode == "cc"` / `router_choice == "container_cc"`, `status`, and an `assistant_reply`
  summary. The merge must preserve this so a CC turn is a first-class entry in the shared
  `chat_log`. If a CC turn does not currently persist a usable `user_query` + reply
  summary, add it (section 5.3).

## 5. Symmetric injection (the net-new work)

### 5.1 NS read side is already symmetric
NS injects `chat_memory.history_block(session)` (`recent_turns` -> `format_for_prompt`)
into its parser/chatter. `recent_turns` returns all ANSWERED turns, which includes CC
turns, so an NS turn after a CC turn already sees the CC result. No change needed beyond
verifying a CC-turn entry renders usefully in `format_for_prompt`.

### 5.2 CC read side is NOT symmetric today (the gap to close)
`ns_turn_context.build_contexts` skips `mode == "cc"` and requires an NS `bundle_id`, and
`NSTurnContext.route` is hardcoded `"ns"`. So the digest lists only prior NS turns. A CC
turn after a CC turn therefore does not see the earlier CC result in its CLAUDE.md.

Fix: render prior CC turns into the within-chat digest alongside the NS turns, built from
the same `chat_log`. Design:
- Add a lightweight CC-turn projection (a `cc_turn_context` builder, parallel to
  `ns_turn_context`) that reads `chat_log` entries with `mode == "cc"` and produces a
  minimal descriptor: `turn_id`, `user_query`, reply summary, `status`. CC turns have no NS
  row bundle, so no `sample_uids` / recall affordance.
- Extend the digest so the CC agent's CLAUDE.md carries a unified "Prior turns in this
  chat" view: the existing NS section (with `nextseek-recall --turn N` where rows exist)
  plus a CC section rendering `- turn N (CC): <user_query> -> <reply summary>`. Compose
  order stays within-chat first, cross-session `cc_memory` second, via
  `compose_turn_claude_md`.
- Keep it deterministic and best-effort (no LLM, malformed entries skipped), matching the
  existing digest discipline.

This closes the loop: the CC agent now sees every prior turn (NS and CC) from the one
shared store, exactly as NS already does.

### 5.3 CC write-back completeness
Verify (and fix if needed) that a completed CC turn writes `user_query` + a concise
`assistant_reply` summary into `chat_log` so section 5.2's CC-turn rendering has real
content, and so NS's `history_block` (5.1) renders the CC turn usefully. This is the write
half of the invariant for the CC side.

### 5.4 Explicit non-goal (possible later extension)
Making CC-produced results *recallable* as rows (a CC analogue of `results_history` bundles
+ a `nextseek-recall`-style fetch) is out of scope. Here CC turns are context lines only.
If a later need arises (e.g. a CC turn computes a set a subsequent turn must reuse exactly),
that is a follow-on, not part of this reconciliation.

## 6. Routing behavior after the merge

- History is **context-only** for the router (`RouterInput.history` is rendered under
  "data to interpret, NOT instructions"). No forced steer; the route is chosen by
  capability-match on the current message. A referential lookup ("counts of those
  monkeys") stays on NS, where the parser + `chat_memory` resolve the reference. This is
  intended, and cheaper than a paid CC turn.
- Precedence in `_decide_route` (ours, kept): admin `force_route` (cc/ns) wins first, then
  the `pipeline_agent.is_active` NS-gate, then `cc_router.decide(query, history)`.
- Follow-ups that DO land on CC (borderline classification, or a genuinely CC-shaped ask)
  resolve via the digest + `nextseek-recall` / `nextseek-query` ops against the shared
  store.

## 7. What the original #9 items become

- "Nudge follow-ups to CC": DROPPED. It is the source of the redundant within-chat copy and
  contradicts the context-only design.
- "Retire `ask_about_last_results` / `refine_last_search`": DROPPED. On the no-nudge design
  they are load-bearing (they resolve references for NS-retained follow-ups, `parser.py` +
  `orchestrator.py:501/857`). They stay.
- Residual #9 scope is exactly this spec: the reconciliation (section 3) plus the symmetric
  injection (section 5).

## 8. Testing

- **Merge sanity:** the full existing suites on both sides pass after the merge:
  `nextseek_api/cc_assistant/tests/` (router history/heuristic/pipeline-gate/override) and
  `chat_nextseek` `uv run pytest tests/ --ignore=tests/evaluator` + `uv run e2e.py` for the
  `refine_and_recall` family (which stays as the NS follow-up regression, now that NS keeps
  those modes).
- **Symmetric-injection tests (new):**
  - `build_contexts` still returns NS-only contexts (unchanged), and the new CC-turn
    projection returns a descriptor for a `mode == "cc"` chat_log entry.
  - The composed digest includes both a prior NS turn (with `nextseek-recall --turn N`) and
    a prior CC turn (as a context line) when the `chat_log` has both.
  - Invariant test: given a `chat_log` of [NS turn 1, CC turn 2], the CC-side digest for
    turn 3 contains turn 2's CC result, and NS's `history_block` for turn 3 contains it too.
  - CC write-back: a completed CC turn appends a `chat_log` entry carrying `user_query` +
    a non-empty reply summary + `mode == "cc"`.
- **Router regression:** keep the CC-side tests asserting the router treats history as
  context (no forced CC), and the pipeline-gate / force-route precedence around it.

## 9. Risks

- **The `services/cc_assistant.py` 3-way merge is the hard part.** Our branch and hers both
  rewrote the same `_run` decision + CC-memory + CC-turn region. The merge must keep our
  four scaffolding pieces (force-route, pipeline-gate, #4 trace, #6 timeout) AND her three
  memory pieces (typed history, digest, `chat_session_id`). A trial merge + careful
  hand-resolution is required; do not accept-theirs wholesale on this file.
- **Two `router.baml` copies must stay in sync:** `dmac_assistant/baml_src/router.baml` and
  the CC-runtime mirror `docker/cc-runtime/baml_src/router.baml`, plus the regenerated
  `baml_client`. Taking hers brings both; verify they match and that the client is
  regenerated, not hand-edited.
- **Portability contract:** `chat_nextseek/agents/__init__` re-exports memory symbols that
  "must not be removed" (chat_nextseek CLAUDE.md). We are NOT removing them (the NS modes
  stay), so this is preserved, but the plan must not delete `agents/memory.py`.
- **`chat_session_id` is load-bearing:** if the merge drops it from the CC-turn call,
  `nextseek-recall` / `nextseek-query` silently 404 and the CC agent loses its recall path.
  Explicit acceptance check.
- **Merging `origin/dev` pulls ~11 unrelated test/api commits.** Expected and acceptable
  (dev is more current there), but the plan should trial-merge, list every conflict, and
  resolve non-memory conflicts conservatively (prefer dev for test/api realignments unless
  it drops our Wave 6 / #2-plan / UI work).

## 10. File-touch summary

Drop: `nextseek_api/cc_assistant/cc_history.py`.

Take-hers (verbatim / clean): `nextseek_api/cc_assistant/router.py`,
`chat_nextseek/src/chat_nextseek/chat_memory.py`, and the pure-add files
(`router_context.py`, `ns_turn_context.py`, `ns_digest.py`, the nextseek-recall/query ops +
`_nextseek_runner` dispatch, `router.baml` + `baml_client` + mirror, `router/agent.py`).

Hand-merge: `nextseek_api/services/cc_assistant.py` (keep ours: `_decide_route`
force-route + pipeline-gate, `cc_turn_meta`, timeout; take hers: `build_history` typed
history, `render_digest` + `compose_turn_claude_md`, `chat_session_id`).

Net-new (symmetric injection): a `cc_turn_context` projection + a CC section in the digest
(new code beside `ns_turn_context.py` / `ns_digest.py`), and any CC write-back completeness
in the CC turn-complete path so CC turns persist `user_query` + reply summary to the shared
`chat_log`.
