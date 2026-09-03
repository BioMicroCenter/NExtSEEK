# Cross-mode shared memory reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile the #8 memory branch with Taisha's `dev` memory system on `dev-v3-merge` (adopt her stack, drop our redundant shim), then close the one asymmetry so both NS and CC read+write one shared store symmetrically.

**Architecture:** Merge `origin/dev` into `dev-v3-merge` with a fixed conflict-resolution policy (drop `cc_history`, take-hers `router.py`/`chat_memory.py`, hand-merge `services/cc_assistant.py` keeping our scaffolding + her memory wiring). Then add a CC-turn projection + a CC section to the within-chat digest so the CC agent sees prior CC turns, matching what NS already gets from `history_block`.

**Tech Stack:** Django (nextseek_api), Python 3.14 + pydantic v2 (chat_nextseek + cc_assistant), pytest / pytest-django, BAML.

**Design spec:** `docs/archive/2026-07/2026-07-23-cross-mode-memory-reconciliation-design.md` (read it first).

## Global Constraints

- **Two test surfaces.** chat_nextseek: from `chat_nextseek/`, `uv run pytest tests/ --ignore=tests/evaluator -q`. nextseek_api (Django): from the repo root, `DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/<file> --no-migrations -q` (run inside the `nextseek` container if local DB setup fails; see the project's Django test recipe: container + `dmac.test_settings` + `--no-migrations`).
- The merge pulls in all of `origin/dev`. **Do NOT drop our branch-only work** (Wave 6 `route_capabilities.json`, the #2 spec/plan docs under `docs/`, the chat_frontend UI, the #4/#6 service features).
- **Two `router.baml` copies must stay identical:** `dmac_assistant/baml_src/router.baml` and the mirror `docker/cc-runtime/baml_src/router.baml`. Never hand-edit `baml_client/`; it is generated.
- **Do NOT remove** `chat_nextseek/agents/memory.py`, the `ask_about_last_results` / `refine_last_search` NS modes, or the `agents/__init__` memory re-exports (portability contract + load-bearing on the no-nudge design).
- **`chat_session_id` is load-bearing:** the CC turn must be spawned with it, or `nextseek-recall` / `nextseek-query` 404.
- History is **context-only** for the router. Do NOT reintroduce a forced follow-up→CC steer.
- Conventional commits, module scopes: `chore(cc)`, `feat(cc)`, `test(cc)`.

---

### Task 1: Perform the reconciliation merge

This task is procedural (git merge + conflict resolution by policy), not TDD. Its deliverable is a merged tree with both test suites green and the acceptance checks below passing.

**Files:**
- Delete: `nextseek_api/cc_assistant/cc_history.py`
- Take-theirs: `nextseek_api/cc_assistant/router.py`, `chat_nextseek/src/chat_nextseek/chat_memory.py`
- Hand-merge: `nextseek_api/services/cc_assistant.py`
- Pure adds from dev (no conflict): `router_context.py`, `ns_turn_context.py`, `ns_digest.py`, the `nextseek-recall`/`nextseek-query` bins + `_nextseek_runner.py`, `dmac_assistant/baml_src/router.baml` + `baml_client` + mirror, `dmac_assistant/src/dmac_assistant/router/agent.py`

**Interfaces (post-merge, relied on by Tasks 2-4):**
- Produces: `nextseek_api.cc_assistant.ns_turn_context.build_contexts(chat_log, results_history, *, session_id) -> list[NSTurnContext]`; `nextseek_api.cc_assistant.ns_digest.render_digest(list[NSTurnContext]) -> str` and `compose_turn_claude_md(digest_md, memory_md) -> str`; `nextseek_api.cc_assistant.router_context.build_history(chat_log, *, limit=5) -> list[HistoryTurn]`.

- [ ] **Step 1: Confirm the starting point**

Run: `git fetch origin && git log --oneline -1 && git status -sb`
Expected: on `dev-v3-merge`, clean tree, `origin/dev` fetched. Note the pre-merge HEAD sha for rollback.

- [ ] **Step 2: Start the merge**

Run: `git merge --no-commit --no-ff origin/dev`
Expected: it stops with conflicts. Capture the full conflict list:
Run: `git diff --name-only --diff-filter=U`
Expected: includes at least `nextseek_api/cc_assistant/router.py`, `nextseek_api/services/cc_assistant.py`. `chat_memory.py` may or may not conflict (ours == merge-base, so it should auto-merge to theirs). Record any non-memory conflicts for Step 6.

- [ ] **Step 3: Resolve the drop + take-theirs files**

```bash
# Drop our redundant shim (it does not exist on dev; if the merge re-added or kept it, remove it)
git rm -f nextseek_api/cc_assistant/cc_history.py
# Take dev's versions of the clean files
git checkout --theirs nextseek_api/cc_assistant/router.py chat_nextseek/src/chat_nextseek/chat_memory.py
git add nextseek_api/cc_assistant/router.py chat_nextseek/src/chat_nextseek/chat_memory.py
# Remove any lingering import of cc_history anywhere
grep -rn "cc_history" nextseek_api | grep -v "/tests/" || echo "no cc_history refs remain (good)"
```
Expected: no `cc_history` references remain in non-test code. If a test references it, delete/rewrite that test (it tested the dropped shim).

- [ ] **Step 4: Hand-merge `nextseek_api/services/cc_assistant.py`**

Open the conflicted file. Resolve so the final file **keeps OURS**:
- `_decide_route(user, req, *, force_cc, session, history)` with the admin `force_route` gate and the `pipeline_agent.is_active(session)` NS-gate (commit 156db94).
- `_merge_extra_state`, the `cc_turn_meta` event-trace metadata (#4), and `clamp_turn_timeout` / `max_turn_length_s` (#6).

and **takes THEIRS** for the memory wiring:
- Build the router history with `router_context.build_history(chat_log)` and thread the typed `list[HistoryTurn]` through `_decide_route` → `cc_router.decide(query, history=...)` (delete our `conversation_history = build_conversation_history(...)` string path).
- In the CC-memory block, build `digest_md = render_digest(build_contexts(chat_log, results_history, session_id=<cc_state_key>))` and `combined = compose_turn_claude_md(digest_md, memory_md)`; write `combined` (not the memory-only `md`) to the turn CLAUDE.md.
- In the CC-turn call, use `query=req.query` (NOT `cc_prompt_with_history(...)`) and pass `chat_session_id=<cc_state_key>`.

Then: `git add nextseek_api/services/cc_assistant.py`

- [ ] **Step 5: Confirm the pure-add files landed**

```bash
for f in nextseek_api/cc_assistant/router_context.py nextseek_api/cc_assistant/ns_turn_context.py \
         nextseek_api/cc_assistant/ns_digest.py dmac_assistant/baml_src/router.baml \
         docker/cc-runtime/baml_src/router.baml \
         docker/cc-runtime/build_context/plugins/nextseek/bin/nextseek-recall \
         docker/cc-runtime/build_context/plugins/nextseek/bin/nextseek-query; do
  test -e "$f" && echo "ok  $f" || echo "MISSING  $f"
done
diff -q dmac_assistant/baml_src/router.baml docker/cc-runtime/baml_src/router.baml && echo "baml copies match"
```
Expected: all `ok`, and the two `router.baml` copies match.

- [ ] **Step 6: Resolve any remaining non-memory conflicts conservatively**

For any other conflicted file (test/api realignments from dev), prefer dev's version UNLESS it would drop our branch-only work. Then `git add` each. Confirm none are outstanding:
Run: `git diff --name-only --diff-filter=U`
Expected: empty.

- [ ] **Step 7: Verify both test suites are green**

```bash
DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/ --no-migrations -q
( cd chat_nextseek && uv run pytest tests/ --ignore=tests/evaluator -q )
```
Expected: PASS. Fix any test that broke because it asserted the dropped shim or our old string-history path (rewrite to the new typed-history / digest behavior). The `refine_and_recall` e2e family stays as-is (NS modes are kept).

- [ ] **Step 8: Acceptance checks (grep the resolved services file)**

```bash
f=nextseek_api/services/cc_assistant.py
grep -q "build_history" $f && echo "ok: typed history" || echo "FAIL: history"
grep -q "render_digest" $f && echo "ok: digest" || echo "FAIL: digest"
grep -q "chat_session_id" $f && echo "ok: chat_session_id" || echo "FAIL: chat_session_id"
grep -q "force_route\|_decide_route" $f && echo "ok: force-route/pipeline gate kept" || echo "FAIL: gate"
grep -q "cc_turn_meta\|clamp_turn_timeout" $f && echo "ok: #4/#6 kept" || echo "FAIL: #4/#6"
grep -q "cc_prompt_with_history\|build_conversation_history" $f && echo "FAIL: shim not removed" || echo "ok: shim removed"
```
Expected: all `ok`.

- [ ] **Step 9: Commit the merge**

```bash
git commit -m "chore(cc): reconcile #8 memory branch with dev — adopt dev stack, drop cc_history shim (#9)"
```

---

### Task 2: `cc_turn_context` projection

**Files:**
- Create: `nextseek_api/cc_assistant/cc_turn_context.py`
- Test: `nextseek_api/cc_assistant/tests/test_cc_turn_context.py`

**Interfaces:**
- Consumes: shared `chat_log` entries. A CC entry (from `serialize_cc_chat_log_entry`) has `{turn_id:int, mode:"cc", user_query, assistant_reply, status:"completed", ts, ...}`.
- Produces: `build_cc_contexts(chat_log) -> list[CCTurnContext]` where `CCTurnContext` has `turn_id:int, route:"cc", user_query:str, reply:str, reply_truncated:bool, status:str, ts:str`.

- [ ] **Step 1: Write the failing test** `tests/test_cc_turn_context.py`:

```python
from nextseek_api.cc_assistant.cc_turn_context import build_cc_contexts, CCTurnContext


def test_build_cc_contexts_projects_answered_cc_turns():
    chat_log = [
        {"turn_id": 1, "mode": "nextseek_query", "user_query": "find NHP", "status": "completed"},
        {"turn_id": 2, "mode": "cc", "user_query": "count those", "assistant_reply": "42 samples",
         "status": "completed", "ts": "t"},
    ]
    ctxs = build_cc_contexts(chat_log)
    assert len(ctxs) == 1
    assert ctxs[0].turn_id == 2 and ctxs[0].route == "cc"
    assert ctxs[0].user_query == "count those" and ctxs[0].reply == "42 samples"


def test_build_cc_contexts_skips_ns_unanswered_and_malformed():
    chat_log = [
        {"turn_id": 3, "mode": "cc", "user_query": "x", "status": "error"},      # not answered
        {"turn_id": "u", "mode": "cc", "user_query": "y", "status": "completed"}, # non-int turn_id
        {"mode": "cc", "user_query": "z", "status": "completed"},                 # no turn_id
        "not a dict",
    ]
    assert build_cc_contexts(chat_log) == []


def test_build_cc_contexts_truncates_long_reply():
    chat_log = [{"turn_id": 1, "mode": "cc", "user_query": "q",
                 "assistant_reply": "x" * 5000, "status": "completed"}]
    ctx = build_cc_contexts(chat_log)[0]
    assert ctx.reply_truncated is True and len(ctx.reply) == 2000
```

- [ ] **Step 2: Run to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/test_cc_turn_context.py --no-migrations -v`
Expected: FAIL with `ModuleNotFoundError: nextseek_api.cc_assistant.cc_turn_context`

- [ ] **Step 3: Create the module** `nextseek_api/cc_assistant/cc_turn_context.py`:

```python
"""Component (issue #9): deterministic CCTurnContext projection.

Mirror of ns_turn_context for Container-CC turns. A CC turn has no NS row bundle,
so it projects the chat_log entry itself (user_query + reply summary) into a
minimal descriptor for the within-chat digest. Pure — no LLM, no DB; malformed or
unanswered entries are skipped (best-effort digest)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter

SCHEMA_VERSION = "ccctx/v1"
_REPLY_CAP = 2000


class CCTurnContext(BaseModel):
    schema_version: str = SCHEMA_VERSION
    turn_id: int
    route: Literal["cc"] = "cc"
    ts: str = ""
    user_query: str
    reply: str = ""
    reply_truncated: bool = False
    status: str = "completed"


CCTurnContextList = TypeAdapter(list[CCTurnContext])


def _is_answered_cc(e) -> bool:
    return (isinstance(e, dict) and e.get("mode") == "cc"
            and e.get("status") == "completed"
            and isinstance(e.get("turn_id"), int) and not isinstance(e.get("turn_id"), bool))


def build_cc_contexts(chat_log) -> list[CCTurnContext]:
    """One context per prior ANSWERED CC turn (mode=='cc', status=='completed')."""
    out: list[CCTurnContext] = []
    for e in (chat_log or []):
        if not _is_answered_cc(e):
            continue
        reply_raw = str(e.get("assistant_reply") or "")
        try:
            out.append(CCTurnContext(
                turn_id=int(e["turn_id"]),
                ts=str(e.get("ts") or ""),
                user_query=str(e.get("user_query") or ""),
                reply=reply_raw[:_REPLY_CAP],
                reply_truncated=len(reply_raw) > _REPLY_CAP,
                status=str(e.get("status") or "completed"),
            ))
        except Exception:  # noqa: BLE001 - digest is best-effort
            continue
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/test_cc_turn_context.py --no-migrations -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/cc_turn_context.py nextseek_api/cc_assistant/tests/test_cc_turn_context.py
git commit -m "feat(cc): CCTurnContext projection for prior CC turns in the digest (#9)"
```

---

### Task 3: CC section + combined within-chat digest

**Files:**
- Modify: `nextseek_api/cc_assistant/ns_digest.py` (add `render_cc_digest` + `render_within_chat_digest`)
- Test: `nextseek_api/cc_assistant/tests/test_ns_digest.py` (create if absent, else append)

**Interfaces:**
- Consumes: `CCTurnContext` (Task 2), `NSTurnContext` + `render_digest` (existing).
- Produces: `render_cc_digest(list[CCTurnContext]) -> str`; `render_within_chat_digest(ns_contexts, cc_contexts) -> str` (NS section then CC section, empty-safe).

- [ ] **Step 1: Write the failing test** in `tests/test_ns_digest.py`:

```python
from nextseek_api.cc_assistant.ns_digest import render_cc_digest, render_within_chat_digest
from nextseek_api.cc_assistant.cc_turn_context import CCTurnContext
from nextseek_api.cc_assistant.ns_turn_context import NSTurnContext, NSResultSummary


def _cc(turn_id, q, reply):
    return CCTurnContext(turn_id=turn_id, user_query=q, reply=reply)


def _ns(turn_id, q, total, uids):
    return NSTurnContext(session_id="s", turn_id=turn_id, bundle_id=turn_id, ts="t",
                         mode="nextseek_query", user_query=q, reply="",
                         result=NSResultSummary(total=total, row_count=len(uids), sample_uids=uids),
                         full_result_available=bool(uids))


def test_render_cc_digest_lists_cc_turns():
    md = render_cc_digest([_cc(2, "count those", "42 samples")])
    assert "Prior Container-CC turns in this chat" in md
    assert "turn 2 (CC): count those" in md
    assert "42 samples" in md


def test_render_cc_digest_empty_when_none():
    assert render_cc_digest([]) == ""


def test_render_within_chat_digest_has_both_sections():
    md = render_within_chat_digest([_ns(1, "find NHP", 139, ["D.SEQ-1"])],
                                   [_cc(2, "count those", "42 samples")])
    # NS section (with recall affordance) AND CC section both present
    assert "Prior NExtSEEK results in this chat" in md
    assert "nextseek-recall --turn 1" in md
    assert "Prior Container-CC turns in this chat" in md
    assert "turn 2 (CC): count those" in md
    # NS section renders before the CC section
    assert md.index("NExtSEEK results") < md.index("Container-CC turns")


def test_render_within_chat_digest_ns_only_when_no_cc():
    md = render_within_chat_digest([_ns(1, "find NHP", 139, ["D.SEQ-1"])], [])
    assert "Prior NExtSEEK results" in md and "Container-CC turns" not in md
```

- [ ] **Step 2: Run to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/test_ns_digest.py --no-migrations -v`
Expected: FAIL with `ImportError: cannot import name 'render_cc_digest'`

- [ ] **Step 3: Add the renderers** to `nextseek_api/cc_assistant/ns_digest.py`. Add the import at the top:

```python
from nextseek_api.cc_assistant.cc_turn_context import CCTurnContext
```

And append after `render_digest`:

```python
CC_DIGEST_HEADER = "## Prior Container-CC turns in this chat"
_CC_REPLY_LINE_CAP = 200


def _render_cc_turn(ctx: CCTurnContext) -> list[str]:
    lines = [f"- turn {ctx.turn_id} (CC): {ctx.user_query}"]
    reply = " ".join(ctx.reply.split())
    if reply:
        if len(reply) > _CC_REPLY_LINE_CAP:
            reply = reply[:_CC_REPLY_LINE_CAP] + "…"
        lines.append(f"  answer: {reply}")
    return lines


def render_cc_digest(contexts: list[CCTurnContext]) -> str:
    """Render prior Container-CC turns as context lines; empty string when none.
    CC turns carry no NS row bundle, so there is no `nextseek-recall` affordance."""
    if not contexts:
        return ""
    capped = len(contexts) > DIGEST_MAX_TURNS
    visible = contexts[-DIGEST_MAX_TURNS:] if capped else contexts
    lines = [CC_DIGEST_HEADER, ""]
    if capped:
        lines.append(f"(showing the {DIGEST_MAX_TURNS} most recent CC turns)")
        lines.append("")
    for ctx in visible:
        lines.extend(_render_cc_turn(ctx))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def render_within_chat_digest(ns_contexts, cc_contexts) -> str:
    """Combined within-chat digest: the NS-results section (with recall) followed
    by the CC-turns section. Either may be empty."""
    return "\n\n".join(p for p in (render_digest(ns_contexts), render_cc_digest(cc_contexts)) if p)
```

- [ ] **Step 4: Run to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/test_ns_digest.py --no-migrations -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/ns_digest.py nextseek_api/cc_assistant/tests/test_ns_digest.py
git commit -m "feat(cc): CC-turn digest section + combined within-chat renderer (#9)"
```

---

### Task 4: Wire the combined digest into the CC turn + prove the invariant

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py` (swap `render_digest(...)` → `render_within_chat_digest(...)` in the CC-memory block)
- Test: `nextseek_api/cc_assistant/tests/test_shared_memory_symmetry.py` (new)
- Test: `chat_nextseek/tests/test_chat_memory_cc_turns.py` (new — the NS read side)

**Interfaces:**
- Consumes: `build_cc_contexts` (Task 2), `render_within_chat_digest` (Task 3), `build_contexts` (existing).

- [ ] **Step 1: Write the failing symmetry tests** `nextseek_api/cc_assistant/tests/test_shared_memory_symmetry.py`:

```python
from nextseek_api.cc_assistant.ns_turn_context import build_contexts
from nextseek_api.cc_assistant.cc_turn_context import build_cc_contexts
from nextseek_api.cc_assistant.ns_digest import render_within_chat_digest
from nextseek_api.cc_assistant.cc_turn_complete import serialize_cc_chat_log_entry, TurnCompletePayload


# Mixed conversation: turn 1 NS (has a bundle), turn 2 CC (answered).
CHAT_LOG = [
    {"turn_id": 1, "mode": "nextseek_query", "user_query": "find NHP sequencing",
     "status": "completed", "bundle_id": 10},
    {"turn_id": 2, "mode": "cc", "user_query": "count sex of those",
     "assistant_reply": "3 male, 2 female", "status": "completed"},
]
RESULTS_HISTORY = [{"id": 10, "user_query": "find NHP sequencing", "endpoint": "/advanced_search",
                    "method": "POST", "api_result_full": {"ok": True, "data": {"total": 139,
                    "rows": [{"uid": "D.SEQ-1"}]}}, "terminal_reply": "139 records"}]


def test_cc_agent_digest_contains_both_ns_and_cc_prior_turns():
    md = render_within_chat_digest(
        build_contexts(CHAT_LOG, RESULTS_HISTORY, session_id="s"),
        build_cc_contexts(CHAT_LOG))
    assert "turn 1 (bundle 10): find NHP sequencing" in md   # prior NS turn visible
    assert "nextseek-recall --turn 1" in md                   # with its recall affordance
    assert "turn 2 (CC): count sex of those" in md            # prior CC turn NOW visible (the fix)
    assert "3 male, 2 female" in md


def test_cc_turn_writeback_carries_query_and_reply():
    payload = TurnCompletePayload(
        chat_session=None, user_query="count sex of those", assistant_reply="3 male, 2 female",
        ts="t", artifacts=None, cc_traces=[], turn_id="run-uuid", cc_session_id="s", raw_jsonl=b"")
    entry = serialize_cc_chat_log_entry(payload, turn_id=2)
    assert entry["user_query"] == "count sex of those"
    assert entry["assistant_reply"] == "3 male, 2 female"
    assert entry["mode"] == "cc" and entry["status"] == "completed"
```

- [ ] **Step 2: Run to verify the digest test fails**

Run: `DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/test_shared_memory_symmetry.py --no-migrations -v`
Expected: `test_cc_turn_writeback...` PASSES already (write-back is done); `test_cc_agent_digest_contains_both...` PASSES too if Tasks 2-3 are in — this test is the acceptance proof that the fix composes. If it fails, fix the renderer wiring from Task 3.

(Note: this task's net-new production change is the services wiring in Step 3; the test above validates the composed behavior end-to-end at the renderer level, which is the shared-store invariant.)

- [ ] **Step 3: Wire it into the service.** In `nextseek_api/services/cc_assistant.py`, in the CC-memory block (the one Task 1 merged), replace the digest construction:

```python
    digest_md = render_digest(build_contexts(chat_log, results_history, session_id=cc_state_key))
    combined = compose_turn_claude_md(digest_md, memory_md)
```

with:

```python
    within_chat_md = render_within_chat_digest(
        build_contexts(chat_log, results_history, session_id=cc_state_key),
        build_cc_contexts(chat_log))
    combined = compose_turn_claude_md(within_chat_md, memory_md)
```

Update the imports at the top of the file:

```python
from nextseek_api.cc_assistant.ns_digest import render_within_chat_digest, compose_turn_claude_md
from nextseek_api.cc_assistant.ns_turn_context import build_contexts
from nextseek_api.cc_assistant.cc_turn_context import build_cc_contexts
```

- [ ] **Step 4: Write the NS-read-side test** `chat_nextseek/tests/test_chat_memory_cc_turns.py` (proves NS also sees CC turns, so both directions are symmetric):

```python
from chat_nextseek.chat_memory import recent_turns, format_for_prompt


def test_ns_history_includes_answered_cc_turns():
    session = {"chat_log": [
        {"turn_id": 1, "mode": "nextseek_query", "user_query": "find NHP", "status": "completed",
         "ts": "t"},
        {"turn_id": 2, "mode": "cc", "user_query": "count those", "assistant_reply": "42",
         "status": "completed", "ts": "t"},
    ]}
    turns = recent_turns(session, n=5)
    assert any(t.get("mode") == "cc" for t in turns)          # CC turn survives the answered filter
    block = format_for_prompt(turns)
    assert "count those" in block                              # and renders into the NS parser context
```

- [ ] **Step 5: Run both suites**

```bash
DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/test_shared_memory_symmetry.py --no-migrations -v
( cd chat_nextseek && uv run pytest tests/test_chat_memory_cc_turns.py -v )
```
Expected: PASS. (If `recent_turns`/`format_for_prompt` signatures differ post-merge, adjust the test to the actual names — they are in `chat_nextseek/chat_memory.py`.)

- [ ] **Step 6: Full-suite regression**

```bash
DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/ --no-migrations -q
( cd chat_nextseek && uv run pytest tests/ --ignore=tests/evaluator -q )
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add nextseek_api/services/cc_assistant.py \
        nextseek_api/cc_assistant/tests/test_shared_memory_symmetry.py \
        chat_nextseek/tests/test_chat_memory_cc_turns.py
git commit -m "feat(cc): inject combined NS+CC within-chat digest — symmetric shared memory (#9)"
```

---

## Final verification

- [ ] Both suites green: `DJANGO_SETTINGS_MODULE=dmac.test_settings uv run pytest nextseek_api/cc_assistant/tests/ --no-migrations -q` and `( cd chat_nextseek && uv run pytest tests/ --ignore=tests/evaluator -q )`.
- [ ] Invariant holds by inspection: for a chat_log of [NS turn, CC turn], the CC-side digest (`render_within_chat_digest`) shows both, and NS's `history_block`/`format_for_prompt` shows both.
- [ ] Acceptance greps from Task 1 Step 8 still pass on the final `services/cc_assistant.py`.
- [ ] **Owner-driven live smoke (out of automated scope):** rebuild `nextseek`, then in the chat UI run a mixed conversation (NS search → a CC turn → a follow-up) and confirm (a) follow-ups are NOT force-nudged to CC, (b) a CC turn can `nextseek-recall` a prior NS turn, (c) a later turn (either mode) sees the prior CC turn.

## Notes for the executor

- Task 1 is a real git merge; if resolution gets tangled, `git merge --abort` and restart from the recorded pre-merge sha. The policy is fixed: drop `cc_history`, take-theirs `router.py`/`chat_memory.py`, hand-merge `services/cc_assistant.py` (ours: gates/#4/#6; theirs: history/digest/chat_session_id).
- Do not reintroduce any follow-up→CC steer, and do not remove the NS `ask_about_last_results`/`refine_last_search` modes — both are deliberate per the spec.
- The exact variable name for the CC session key in `services/cc_assistant.py` may be `cc_state_key` or similar; use whatever the merged file already uses for the session id, and pass that same value as `chat_session_id` and as `build_contexts(..., session_id=...)`.
- If pytest-django cannot build the test DB locally, run the nextseek_api tests inside the `nextseek` container per the project's Django test recipe (`dmac.test_settings` + `--no-migrations`).
