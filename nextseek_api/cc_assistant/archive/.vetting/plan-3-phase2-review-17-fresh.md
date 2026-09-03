# PLAN-3 Phase 2 Review — iter 17 (fresh, canonical prompt)

**Target:** `PLAN-3-ui-based-io.md`  
**Locked design:** `SPEC-3-ui-based-io.md`  
**Reviewer:** Independent cold-context adversarial (2026-06-30)  
**Note:** Reviewer ran in Ask mode; findings persisted by orchestrator from subagent transcript.

---

## 2A — Vet

Permissions catalogue matches live repo. No missing credential found. Non-blocking: no disk-quota ops note for accumulating uploads/artifacts.

---

## 2B/2D — Key Findings

### CRITICAL — `cc_turn_complete.py` paste crashes on import (`NameError: ChatSession`)
**Location:** Task 11 typed callback paste — `@dataclass class TurnCompletePayload: chat_session: ChatSession`
**Why:** No `from __future__ import annotations`; `ChatSession` not imported. Reproduced `NameError` on class definition.
**Fix:** Add `from __future__ import annotations` at top of module.

### HIGH — Unbounded `chat_log` / duplicated `cc_traces` in `extra_state`
**Location:** Task 11a `_append_cc_turn_complete` — append + `traces.extend(payload.cc_traces)`
**Why:** NS path caps at `MAX_TURNS=50`; CC has no cap and stores traces twice (chat_log + mirror). Defeats SPEC rationale for separate transcript table.
**Fix:** FIFO cap `chat_log` at 50; drop separate `es["cc_traces"]` mirror — traces live in `chat_log[]` only.

### HIGH — `downloadCcArtifact` returns unused Blob
**Location:** Task 12 `chatApi.ts` paste
**Why:** `downloadArtifact` uses object URL + anchor click (`Promise<void>`). CC variant returns Blob; wiring never saves file.
**Fix:** Mirror native download behavior; `Promise<void>`.

### HIGH — No backend `mode` on live `query_complete` WS payload
**Location:** Task 12 Step 2 — "attach mode: cc from WS payload"
**Why:** Backend never emits `mode`; shared NS/CC handler can't discriminate. Hardcoding "cc" breaks native downloads.
**Fix:** CC `query_complete` payload must include `"mode": "cc"` from `cc_engine.py`.

### MEDIUM — Duplicate upload basenames silently overwrite
**Location:** Task 9 `run_cc_upload_task`
**Fix:** Reject duplicate basenames in one batch or disambiguate; hermetic test.

### MEDIUM — Re-raise on persist failure after paid turn
**Location:** Task 11 failure policy
**Fix:** Acknowledge cost-doubling risk in Risk Register.

---

## 2C — External Dependencies

zstandard, pydantic/orjson trace extractor, cc_artifacts — literal paste code verified OK in sandbox. Defect isolated to untested `cc_turn_complete` snippet.

---

## Summary

| Severity | Count |
|----------|------:|
| CRITICAL | 1 |
| HIGH | 3 |
| MEDIUM | 2 |

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
