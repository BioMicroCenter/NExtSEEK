# PLAN-3 Phase-2 fix-log — iter-20 hardener

Target: `PLAN-3-ui-based-io.md` (edited ONLY this file).
Source of findings: `.vetting/plan-3-phase2-review-20-fresh.md` (CONDITIONAL_ACCEPTANCE).
User decision honored (2026-06-30): **RESTORE the `cc_traces` mirror to honor locked SPEC-3 E5.**
Scope owned: thread C (cc_traces mirror reconciliation) + PLAN-3-only MEDIUMs 2A/2B/2D + the in-scope LOW.
NOT touched (separate hardener / orchestrator owns): the PLAN-7 validator marker handshake (review 2B-HIGH, Task 13 Step 8), SPEC-3, `.vetting/defect-lineage.md`.

---

## Finding 1 — HIGH (thread C): mirror contradiction + locked-design deviation → RESTORED + GUARDED

**Locked authority honored (SPEC-3 §6.5 / E5, quoted verbatim):**
- §6.5 Persist: *"append the `CCTrace` (as `model_dump()`) to `ChatSession.extra_state["cc_traces"]` … Keep it small (it is loaded on every session read; full fidelity lives in §7)."*
- E5: *"Display trace storage: `ChatSession.extra_state["cc_traces"]` (no migration)."*

**All four sites reconciled to "mirror IS mandatory" + the paste now writes it:**

| Site | Before | After |
|---|---|---|
| Task 11 Step 5 header (~:1734) | "**sole owner** of `chat_log` and `CCSessionTranscript` upsert; **traces stored in `chat_log[]` entries only**" | "**sole owner** of `chat_log`, the locked-E5 `es["cc_traces"]` mirror, and the `CCSessionTranscript` upsert; the per-turn trace is written to **BOTH** stores in the **same** single RMW save" |
| Task 11 Step 5 bullet 2 (~:1736) | "Mirror append to `es["cc_traces"]` per locked **E5** (mandatory, not optional)." | "**Mirror append** the per-turn trace to `es["cc_traces"]` per locked **E5** (mandatory, not optional; same RMW save as the `chat_log` append; kept small per §6.5 …)" |
| Task 11a header "Reload vs E5" (~:1908) | already "Also mirror append to `extra_state["cc_traces"]` per locked E5 (both stores updated in one RMW save)." | **unchanged** (already consistent) |
| Task 11a interface (~:1956) | "**do not** mirror into separate `es["cc_traces"]` (trace data lives in `chat_log[]` only …)" | "**also mirror** the per-turn trace into `es["cc_traces"]` per locked **E5** (mandatory — both stores written in the **SAME** single RMW save …), while `chat_log[].cc_traces` remains the reload source of truth" |

**The paste now appends the mirror in the SAME single RMW save.** The dual-store mutation was extracted into a NEW pure, Django-free helper `apply_turn_to_extra_state(extra_state, payload, *, cap)` in `cc_turn_complete.py` (Task 11 paste) — mirroring the plan's own existing pattern (`append_capped` extracted "so the boundary is unit-tested … an inline mutation would otherwise pass every check"). New helper body:

```python
def apply_turn_to_extra_state(extra_state, payload, *, cap=50) -> dict:
    es = dict(extra_state or {})
    es["chat_log"] = append_capped(
        list(es.get("chat_log") or []), serialize_cc_chat_log_entry(payload), cap=cap)
    cc_traces = list(es.get("cc_traces") or [])
    for tr in payload.cc_traces:                 # locked E5 mirror
        cc_traces = append_capped(cc_traces, tr, cap=cap)
    es["cc_traces"] = cc_traces                   # <-- the restored E5 mirror line
    return es
```

`_append_cc_turn_complete` (Task 11a) now calls it inside the one RMW save:
```python
session.extra_state = apply_turn_to_extra_state(session.extra_state, payload, cap=MAX_CC_CHAT_LOG_TURNS)
session.save(update_fields=["extra_state", "updated_at"])   # ONE save writes BOTH stores
```
RMW pattern copied verbatim from the canonical owner `services/cc_assistant.py:65-72` (`es = dict(sess.extra_state or {})` → mutate → `sess.extra_state = es` → `save(update_fields=["extra_state","updated_at"])`; never mutate in place).

**Guard test added (hermetic, mutation-sensitive)** — `test_apply_turn_writes_chat_log_and_cc_traces_mirror` in `test_cc_chat_log_writer.py` (Task 11a Step 1, item 3):
```python
es = apply_turn_to_extra_state({}, payload, cap=50)
assert es["chat_log"][-1]["cc_traces"] == [trace]   # reload SoT NOT regressed
assert es["cc_traces"] == [trace]                    # locked-E5 mirror restored
```
**Mutation that goes RED:** deleting `es["cc_traces"] = cc_traces` (or the mirror loop) makes `es["cc_traces"]` absent → second assertion raises `KeyError` → test FAILS. The first assertion simultaneously confirms `chat_log[].cc_traces` (reload source of truth) is still written, so the mirror cannot be "restored" by silently dropping the chat_log carry. Django-free (`TurnCompletePayload` is a plain `@dataclass` under `from __future__ import annotations`; `chat_session=None` is untouched by the pure transform).

---

## Finding 2A — MEDIUM: persist block referenced un-imported symbols (NameError at the paid live gate)

Real source re-read — `cc_engine.py` module top (verified `:22-35`) imports: `json, logging, os, re, shutil, threading, Mapping, Path, Any/Callable, .attach.BridgeAttachSocket, .translate.CCStreamTranslator, .cc_config.CCPaths, cc_session`. It does **not** import `cc_summary`, `cc_trace`, `django.utils.timezone`, or `TurnCompletePayload` — exactly the four the persist block uses.

**Before:** the block opened with only `import time`.
**After:** local imports added at the top of the block (copied verbatim from canonical module paths):
```python
    import time
    from django.utils import timezone
    from . import cc_summary, cc_trace
    from .cc_turn_complete import TurnCompletePayload
```
Kept **local** (not module-top) on purpose: `import cc_engine` stays Django-settings-free for the hermetic `test_cc_newest_jsonl` (`timezone.now()` reads `settings.USE_TZ`). Mutation/oracle: without these the block `NameError`s at the Task 13 live gate; now the names resolve at runtime.

---

## Finding 2B — MEDIUM: projection passthrough unguarded → hermetic guard added

Real projection re-read — `services/assistant.py:521-529` builds `Turn(bundle_id=…, user_query=entry.get("user_query",""), reply=reply, mode=entry.get("mode",""), ts=entry.get("ts"), artifacts=artifacts or None,)`; Task 7 Step 4 adds `cc_traces=entry.get("cc_traces") or None,`. The projection lives inside the DRF `get_session` `@action` (not callable without a DB), so a source-text guard is the right hermetic tool — the same pattern the plan already uses for the Task 11a `assistant_reply` key.

**Added** `test_projection_passes_cc_traces_through` in `test_turn_cc_traces.py` (Task 7 Step 1) + a pointer note after Step 4:
```python
src = (Path(__file__).parents[2] / "services" / "assistant.py").read_text()
assert 'cc_traces=entry.get("cc_traces")' in src
```
`parents[2]` resolves `tests → cc_assistant → nextseek_api`, then `services/assistant.py` — verified path. **Mutation that goes RED:** deleting the `cc_traces=entry.get("cc_traces")` passthrough removes the substring → assertion FAILS (previously only the paid Task 13 reload caught it). Picked up automatically by the existing Task 7 Step 5 verify command (runs the whole file).

---

## Finding 2D — MEDIUM: fixture `# <path>` header inflated `line_count` to 7

Re-read the asserting test: `test_envelope_counts_reuse_parsed_transcript` asserts `t.transcript_line_count == p.line_count == 6`; `cc_summary.parse_transcript` keeps every non-empty line (mapping unparseable → `{"_type":"unparsed"}`) and counts it.

**Before:** the fenced fixture's first line was `# nextseek_api/.../cc_transcript_sample.jsonl` → a literal paste yields 7 jsonl lines → `line_count == 7` breaks the `== 6` assertion.
**After:** the `# <path>` line was removed from inside the fence; the filename moved to prose with an explicit bold instruction — *"EXACTLY these 6 jsonl records and NOTHING ELSE — no `#` path/header comment line"* — plus the mechanism (why a `#` line counts). The "Line 6 …" note was corrected to "the **6th** record — the `summary` line". The Step 9b multitool fixture got the same *"jsonl records only, no `#` header/comment line"* caution. A literal paste now yields exactly 6 lines and the `== 6` assertion passes.

---

## In-scope LOW notes

- **Task 4 `action="modified"` branch unexercised (review 2B-LOW):** extended `test_action_from_diff_and_status_from_tool_result` with a second `extract_trace(..., files_created=[], files_modified=["report.md"])` call asserting `w2.action == "modified"`. This now executes the `elif base in modified_base: action = "modified"` line (`cc_trace.py` Step 8 paste), protecting the `--cov-fail-under=95` floor. No test-count churn (assertion added to the existing test).
- **Task 12 `useMessages.ts:88` line drift (cosmetic):** changed `hooks/useMessages.ts:88` → `hooks/useMessages.ts` (`hydrateFromTurns` map) to drop the contested line number (symbol confirmed present by the reviewer).

---

## Verification table (before → after, re-read after edit)

| # | Defect | Edit landed | Mutation → RED oracle |
|---|---|---|---|
| 1 | mirror contradiction + E5 deviation | 4 sites reconciled; `apply_turn_to_extra_state` writes mirror in 1 RMW; guard test added | drop `es["cc_traces"]=` → `test_apply_turn_writes_chat_log_and_cc_traces_mirror` KeyError |
| 2A | un-imported `cc_summary`/`cc_trace`/`timezone`/`TurnCompletePayload` | 4 local imports added to persist block | absence → NameError at Task 13 live gate |
| 2B | projection passthrough unguarded | `test_projection_passes_cc_traces_through` source guard | drop passthrough line → substring absent → FAIL |
| 2D | fixture `#`-header line-count trap | header removed from fence + explicit no-`#` instruction (Step 5 + 9b) | literal paste now == 6 lines → `== 6` passes |
| LOW | `action="modified"` uncovered | extra assertion in Task 4 test | branch now executed → keeps ≥95% floor |
| LOW | `useMessages.ts:88` drift | line number dropped | n/a (cosmetic) |

**No-contradiction check:** `grep -ni 'do not.*mirror|mirror into separate|chat_log\[\] only|traces stored in'` → "NO CONTRADICTIONS REMAIN".

## Defects I could NOT fully resolve (with reason)
None in scope. Two review notes intentionally left to their owners:
- review **2B-HIGH** (PLAN-7 marker handshake / Task 13 Step 8) — out of scope; owned by the separate marker hardener.
- review cosmetic "Phase-2 Vetting Log skips iteration #24" — the vetting-log numbering is orchestrator/ledger territory; left untouched to avoid clashing with the ledger owner.

## Follow-up suggestions (collateral NOT applied here)
- `cc_engine.py` runtime docstring (`:417-420`) still says "scoped Dropbox mounts … augmented with published host paths" — stale after Task 6/8; a one-line refresh belongs in the Task 6/8 code edit, not the plan.
- The persist block has a redundant local `import shutil` (already at module top `:26`); harmless, left as-is to avoid collateral churn.
