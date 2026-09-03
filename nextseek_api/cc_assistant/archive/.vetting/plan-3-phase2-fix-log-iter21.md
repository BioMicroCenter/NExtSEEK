# PLAN-3 Phase-2 hardening — fix-log (iter-21)

Source review: `.vetting/plan-3-phase2-review-21-fresh.md` (CONDITIONAL_ACCEPTANCE; 0 CRITICAL, 0 HIGH, 2 MEDIUM, 2 LOW + cosmetic notes).
Target edited: `PLAN-3-ui-based-io.md` only. SPEC-3, PLAN-7, the Vetting Log table, the Phase-2 status line, and `.vetting/defect-lineage.md` were NOT touched.

## Verification table (before → after)

### Finding 1 — MEDIUM (2D): no guard that the persist wiring is actually invoked/wired

Canonical strings re-read VERBATIM from the plan's own pastes (these wirings are *introduced* by PLAN-3, so the plan is the canonical owner; cross-checked against the real, currently-unwired sources):
- Persist-block paste, Task 11 "Minimal persist block": `on_turn_complete(TurnCompletePayload(` (call that writes the trace).
- Task 11 Step 3 wiring paste: `on_turn_complete=_append_cc_turn_complete` (kwarg passed into `run_cc_turn`).
- Real `nextseek_api/services/cc_assistant.py:337` `cc_engine.run_cc_turn(...)` call site currently has **no** `on_turn_complete` kwarg; real `cc_engine.py` `run_cc_turn` (`:398`) signature has no `on_turn_complete` param. Both are added by Task 11 Steps 2/3 — so the guards correctly go RED if that wiring is removed.

| | Before | After |
|---|---|---|
| Task 11 Step 4 | "Run `test_newest_jsonl_under_*` + regression suite." (no wiring guard anywhere — a stubbed/unwired callback passes every hermetic test) | Step 4 now appends two source-text grep guards to `test_cc_newest_jsonl.py` and runs them + regression suite |

New guard tests (appended to `nextseek_api/cc_assistant/tests/test_cc_newest_jsonl.py`, read-by-path so they stay Django-free/hermetic):

```python
_NSAPI = Path(__file__).resolve().parents[2]   # .../nextseek_api

def test_cc_engine_actually_invokes_on_turn_complete():
    src = (_NSAPI / "cc_assistant" / "cc_engine.py").read_text()
    assert "on_turn_complete(TurnCompletePayload(" in src

def test_services_wires_append_cc_turn_complete_into_run_cc_turn():
    src = (_NSAPI / "services" / "cc_assistant.py").read_text()
    assert "on_turn_complete=_append_cc_turn_complete" in src
```

RED-mutation proof:
- Delete/stub the `on_turn_complete(TurnCompletePayload(...)` call in `cc_engine.py` → `test_cc_engine_actually_invokes_on_turn_complete` fails (substring absent).
- Remove `on_turn_complete=_append_cc_turn_complete` from the `run_cc_turn(...)` call in `services/cc_assistant.py` → `test_services_wires_append_cc_turn_complete_into_run_cc_turn` fails.
Each string is byte-identical to the plan's canonical paste, so the guard tracks exactly the wiring an implementer must write. No new test file registered (appended to an existing Task 11 file already in the File Structure + commit).

### Finding 2 — MEDIUM (2B / thread E): persist-policy consistency (unconditional raise contradicts locked best-effort)

Every site describing the missing-jsonl / persist-failure policy, reconciled to the single locked rule **"raise only under `CC_PERSIST_STRICT`, else log+deliver"**:

| Site | Before | After | Already-compliant? |
|---|---|---|---|
| Task 11 Step 1 (the defect) | "On missing jsonl after **3× 200ms retry**, raise `RuntimeError`." (unconditional) | "...follow the locked best-effort policy (Step 6 / 'Empty/missing jsonl policy' / the persist-block paste): **deliver the reply without a persisted trace and log at error level; raise `RuntimeError` only under `CC_PERSIST_STRICT`** — a paid, successful reply is never converted to `query_error` by a persist miss." | FIXED |
| Task 11 Step 6 "Failure policy" (line ~1740) | best-effort; hard re-raise only behind `CC_PERSIST_STRICT` | unchanged | compliant |
| Minimal persist-block paste (`else:` branch) | `if strict: raise RuntimeError(...)` | unchanged | compliant |
| "Empty/missing jsonl policy" note | "raised **only** when `CC_PERSIST_STRICT` is set" | unchanged | compliant |
| Risk Register row 9 (`| 9 | 11 | Best-effort persist ... |`) | best-effort; never `query_error` | unchanged | compliant |
| Coverage-exceptions row 11 | "Re-raise in dev; Task 13 reload asserts `cc_traces`" (ambiguous) | "Best-effort: re-raise **only** under `CC_PERSIST_STRICT` (dev/test), else log+deliver; Task 13 reload asserts `cc_traces`" | TIGHTENED |
| Task 13 live-gate prose (line ~2247) | best-effort; reload assertion is the acceptance gate | unchanged | compliant |

Post-edit grep `retry, raise \`RuntimeError\`` / unconditional-raise wording → **NONE remain**. The only literal `raise RuntimeError(...)` in a persist context (persist-block `else` branch) is inside `if strict:`. The `raise ValueError("bad transcript basename")` is basename validation, not persist policy (correctly untouched).

### Finding 3 — LOW (2A): `cc_artifacts` ≥95% gate wired to the wrong test file

Gated module confirmed = `nextseek_api.cc_assistant.cc_artifacts`. Both files exercise it: `test_cc_artifacts_split.py` directly drives `partition_changed`/`build_artifact_zip`/`RAW_PREFIX`; `test_cc_engine_publish.py` drives the `_publish_artifacts` rework that imports `cc_artifacts`.

| Before (Step 5b) | After (Step 5b) |
|---|---|
| coverage appended onto a run of **only** `test_cc_engine_publish.py` | dedicated coverage command naming **both** files (mirrors Task 3's multi-file pattern) |

Corrected coverage command (now in Step 5b):
```bash
uv run --no-project --with pytest --with pytest-cov python -m pytest -q --noconftest \
  nextseek_api/cc_assistant/tests/test_cc_artifacts_split.py \
  nextseek_api/cc_assistant/tests/test_cc_engine_publish.py \
  --cov=nextseek_api.cc_assistant.cc_artifacts --cov-fail-under=95
```

### Finding 4 — LOW (inventory): File Structure path/omission

Verified against the real tree:
- `chat_frontend/src/hooks/useChatApi.ts` exists; `chat_frontend/src/components/ChatPanel/ReportArtifacts.tsx` exists.

| Location | Before | After |
|---|---|---|
| File Structure "Modify" (line 62) | omits `useChatApi.ts`; bare `ReportArtifacts.tsx` | adds `hooks/useChatApi.ts`; `components/ChatPanel/ReportArtifacts.tsx` |
| Task 12 "Modify" (line ~2083) | bare `ReportArtifacts.tsx` | `components/ChatPanel/ReportArtifacts.tsx` |

### Finding 5 — cosmetic notes

- (c) Stale `run_cc_turn` docstring (`cc_engine.py:417-420`, verbatim re-read: `"Execute one Container-CC turn with scoped Dropbox mounts + artifact publish."` / `"reply augmented with published host paths"`): added a "Docstring hygiene (same edit)" note to Task 6 Step 6 instructing the implementer to update it to UI-based I/O in the touched commit. FIXED.
- (a)/(b) File-Structure useChatApi/ReportArtifacts: covered by Finding 4.
- (d) Vetting-Log iteration numbering skips 24 (23→25): **OUT OF SCOPE** — the Vetting Log table is explicitly off-limits per the assignment. Left unchanged by design (noted here, not a silent omission).

## Sites reconciled for thread E (explicit list)
1. Task 11 Step 1 (FIXED — was the only unconditional raise).
2. Task 11 Step 6 "Failure policy" (already compliant).
3. Minimal persist-block paste `else:` branch (already compliant — `if strict:`).
4. "Empty/missing jsonl policy" note (already compliant).
5. Risk Register row 9 (already compliant).
6. Coverage-exceptions row 11 (TIGHTENED).
7. Task 13 live-gate prose (already compliant).
Confirmed: **no unconditional-raise statement remains anywhere** in the persist-failure policy.

## Collateral / follow-ups
- None. All edits are surgical and confined to the assigned findings. No SPEC-3 amendment, no gate lowered (Finding 3 keeps `--cov-fail-under=95`; it only widens the measured test set).

---

Self-verification: re-read [6 sections], confirmed [5/5 defects landed] (4 findings + cosmetic note (c); note (d) is out-of-scope by assignment), introduced [0 collateral], cross-checked [2 wiring contracts + 1 docstring] verbatim. Locked-design alignment: [verified] (SPEC-3 §6.5/§7 lock the write path, not a re-raise-and-discard — best-effort is plan-level). Thread E (persist policy) fully reconciled: [yes]. Defects unresolved: [Vetting-Log numbering skip 23→25 — intentionally not fixed, the Vetting Log table is out of edit scope].
