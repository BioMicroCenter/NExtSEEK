# PLAN-3 Phase 2 Pre-Execution Review — Iter 11 (Fresh, Cold Context)

**Date:** 2026-06-30  
**Target:** `PLAN-3-ui-based-io.md`  
**Locked spec:** `SPEC-3-ui-based-io.md`  
**Iter-10 hardening under test:** min_mtime+jsonl retry in persist block, `run_cc_turn` wiring paste, `cc_endpoint_guards` implementation, zip relpath test, `validate_cc_acceptance` turn-scoped update.

**Live spot-checks (claim verification only):** `cc_engine.py:573-588` (Dropbox + list return), `cc_engine.py:639-672` (`_publish_artifacts` list shape), `services/cc_assistant.py:337-349` (`run_cc_turn` call — no `chat_session`/`on_turn_complete` kwargs yet), `services/cc_assistant.py:77-93` (`_session_metas` global-mtime `rglob`), `translate.py:26+` (no `_turn_start_ts`), `validate_cc_acceptance.py:121-130` (still `{user_id}/` publish check), `test_cc_realstack.py:190` (`artifacts_published`), `cc_provision.py:60-76` (no `input_mnt`), `chat_frontend/src/hooks/useChatApi.ts:27-69` (no sync session-id export), `AppLayout.tsx:42,62-64` (dual `NextseekApiService` instances), `chatApi.ts` at `lib/services/chatApi.ts` (not `lib/api/`).

---

## Severity Summary

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 6 |
| LOW | 2 |

---

## 2A — Vet (permissions & execution snags)

### Finding 2A-1 — HIGH — Task 11 persist block code fence breaks mid-snippet

**Location:** Task 11 “Minimal persist block” (lines ~1584–1622).

**Why defect:** The fenced block closes after `raw = jsonl_path.read_bytes() if jsonl_path else b""` (line ~1596). Lines `parsed = cc_summary.parse_transcript(...)` through the `RuntimeError` else branch sit **outside** the fence with misleading indentation. A subagent pasting literally gets a syntax error or code that runs outside the `if event == "query_complete"` guard — persist never fires or crashes the turn handler.

**Remedy:** One continuous fenced block from the outer `if` through `raise RuntimeError(...)`; no premature closing ```.

---

### Finding 2A-2 — HIGH — `_turn_start_ts` never assigned; `min_mtime` filter is wrong at persist time

**Location:** Task 11 persist block line ~1587: `turn_start = getattr(translator, "_turn_start_ts", time.time())`.

**Live spot-check:** `CCStreamTranslator` in `translate.py` has no `_turn_start_ts` attribute anywhere in the plan or live code.

**Why defect:** Fallback sets `turn_start` to **persist-time** `time.time()`, so `min_mtime = turn_start - 1` is “now minus one second.” The jsonl is written during container execution, then `_publish_artifacts` walks scratch (seconds of I/O) before persist. On any turn where the last jsonl write was >1s before the retry loop, `_newest_jsonl_under(..., min_mtime=...)` returns **None**, triggering `RuntimeError("cc persist: missing transcript jsonl...")` and failing Task 13 Step 6. Even when it “works,” the filter does not exclude stale jsonls from prior turns/resume trees — the iter-10 hardening goal is not met.

**Remedy:** Mandate `translator._turn_start_ts = time.time()` (or monotonic equivalent) **before** `client.containers.run(...)` in `run_cc_turn` Step 1; persist block must use that value, not persist-time `time.time()`.

---

### Finding 2A-3 — MEDIUM — `run_cc_turn` wiring paste incomplete

**Location:** Task 11 Step 3 (lines ~1644–1656).

**Why defect:** Shows `on_turn_complete=_append_cc_turn_complete` but `_append_cc_turn_complete` is only specified in Task 11a (no paste-ready body in Task 11). Missing from wiring snippet: imports (`TurnCompletePayload`, `Callable`), `run_cc_turn` signature extension in `cc_engine.py`, and explicit note that `chat_session` / `req.query` are closure-captured from `_run`. Subagent executing Task 11 Step 2 before 11a can wire a NameError or no-op callback.

**Remedy:** Paste full `_append_cc_turn_complete` in Task 11a before Task 11 Step 2; add `run_cc_turn` signature diff to Task 11 Step 1.

**Iter-10 status:** Partially fixed (wiring one-liner present); still incomplete.

---

### Finding 2A-4 — MEDIUM — `validate_cc_acceptance` turn-scoped update still prose-only

**Location:** Task 6 Step 5c; Task 6 Step 8 commit manifest includes `validate_cc_acceptance.py`.

**Live spot-check:** `validate_cc_acceptance.py:121-130` still validates `published_files.json` paths under `{user_id}/` — incompatible with turn-scoped `output/artifacts/<turn_id>/` and structured `artifacts` keys.

**Why defect:** Step 5c says “replace … with turn-scoped `data["artifacts"]`” but supplies **no updated check function**. Commit lists the file without new semantics → Task 6 hermetic suite either always fails check 16 or passes on the wrong artifact shape (gameable).

**Remedy:** Paste the replacement check (assert `artifacts[].key` matches `{turn_id}/…`, or validate `artifacts.json` in evidence bundle); drop `{user_id}/`-only copier check.

**Iter-10 status:** **NOT FIXED** — file in commit manifest only.

---

## 2B — Stress test

### Finding 2B-1 — MEDIUM — Task 6 coverage: global ≥95% vs Step 5b “optional”

**Location:** Global Constraints line ~30 vs Task 6 Step 5b.

**Why defect:** Global requires `--cov-fail-under=95` for Tasks 1, 3–7; Task 6 Step 5b marks cc_artifacts coverage as **optional**. Subagent can skip coverage on `cc_artifacts.py` without violating the task text while violating global constraints.

**Remedy:** Make Step 5b coverage mandatory (match Task 1 pattern) or narrow the global constraint list.

---

### Finding 2B-2 — MEDIUM — `test_cc_realstack.py` update still unspecified beyond prose

**Location:** Task 6 Step 5c; live `test_cc_realstack.py:190-212` asserts `artifacts_published` and `self.assertTrue(published)`.

**Why defect:** Plan names the update but does not paste the new assertions (`data["artifacts"]`, turn-scoped keys). Combined with 2A-4, the paid realstack gate and zero-spend validator stay on the pre-Step-3 contract after Task 6 lands.

**Remedy:** Paste updated realstack evidence write + assertions mirroring new `query_complete` shape.

---

### Finding 2B-3 — LOW — File Structure path typo for chatApi

**Location:** File Structure line ~58 lists `lib/api/chatApi.ts`; Task 12 and live code use `lib/services/chatApi.ts`.

**Why defect:** Minor navigation trap; Task 12 body is correct.

**Remedy:** Fix File Structure line to `lib/services/chatApi.ts`.

---

## 2C — Validate external dependencies

### Finding 2C-1 — MEDIUM — Celery registration correct but still live-gated only

**Location:** Task 9 Step 3b; live `batch_upload/celery_app.py` has no `cc_upload_tasks` import yet (expected pre-impl).

**Why defect:** Plan correctly documents explicit worker import + Task 13 `inspect registered` grep. Hermetic suite cannot prove registration; a missed Step 3b commit passes all unit tests while upload 202s never execute (Risk Register #2).

**Remedy:** Accept live gate; add a hermetic import guard test that `celery_app` module source contains the `cc_upload_tasks` import after Task 9 (grep/source test, no broker).

**Iter-10 status:** Documented; not new, still MEDIUM process risk.

---

### Finding 2C-2 — LOW — Task 10 guard test snippet omits `import pytest`

**Location:** Task 10 Step 0 test block.

**Why defect:** `pytest.raises` used without import in paste snippet — trivial fix at implement time.

**Remedy:** Add `import pytest` to the test module paste.

---

## 2D — Gameproof

### Finding 2D-1 — MEDIUM — Task 11a `serialize_cc_chat_log_entry` referenced, not defined

**Location:** Task 11a Step 1.

**Why defect:** Step 1 requires a failing test on `serialize_cc_chat_log_entry(payload)` but no function signature or body is pasted. Implementer may inline dict construction with wrong keys (`reply` vs `assistant_reply`), failing reload hydration (Gameability row 11a).

**Remedy:** Paste `serialize_cc_chat_log_entry` returning `{user_query, assistant_reply, mode, ts, artifacts, cc_traces, turn_id}`.

---

### Finding 2D-2 — MEDIUM — Task 12 CC API methods named, not specified

**Location:** Task 12 Interfaces — `uploadFiles`, `pollUpload`, `downloadCcArtifact`.

**Live spot-check:** `chatApi.ts` has no CC upload/download methods today.

**Why defect:** UploadControl and artifact download branch depend on these methods; no URLs, FormData shape, poll interval, or error handling pasted. Cheapest fake: stub methods that resolve without hitting endpoints; Task 13 Step 4/5 “passes” with broken I/O.

**Remedy:** Paste method bodies mirroring Task 9/10 endpoints (multipart POST, GET status poll, GET artifact download with `?key=`).

---

### Finding 2D-3 — MEDIUM — AppLayout dual `NextseekApiService` not fully resolved

**Location:** Task 12 Step 0/5; live `AppLayout.tsx:42` (`useChatApi`) vs `:62` (`useState(() => new NextseekApiService(...))` for `useSessions`).

**Why defect:** Step 5 warns “Do not use AppLayout's separate `useSessions` service” for CC artifact download but does not require **one shared service instance** for submit + sessions + download. `getAuthoritativeSessionId()` from `useChatApi` can disagree with `sessions` service's `_sessionId` → wrong session on download or 404 on 3e promotion.

**Remedy:** Refactor AppLayout to pass `useChatApi`'s `apiService` into `useSessions({ service: apiService })` and remove the duplicate `useState` service.

---

### Finding 2D-4 — LOW — Task 13 live gate remains sound (not gameable if enforced)

Task 13 requires committed `live_gate_transcript.txt`, reload non-empty `cc_traces`, Celery upload completion, and two-turn artifact byte check. Correct real-artifact oracle per ultraplan 2D doctrine. **Blocked** until 2A-1/2A-2 persist path is paste-correct.

---

## Iter-10 Hardening Scorecard

| Claim | In plan? | Iter-11 status |
|-------|----------|----------------|
| min_mtime + 3×200ms retry in persist block | Yes (lines ~1587–1594) | **Partial** — loop present but `_turn_start_ts` unset + code fence break (2A-1, 2A-2) |
| `run_cc_turn` wiring paste | Yes (Step 3) | **Partial** — callback wiring without 11a body/signature (2A-3) |
| `cc_endpoint_guards` paste-ready | Yes (Task 10 Step 0) | **Fixed** |
| Zip relpath test aligned with `build_artifact_zip` | Yes (line ~780) | **Fixed** |
| `validate_cc_acceptance` turn-scoped update | Step 5c prose + commit list | **NOT FIXED** (2A-4) |

---

## Top Findings (priority order)

1. **2A-1 (HIGH)** — Task 11 persist snippet code fence breaks; paste-trap for syntax/scope errors.
2. **2A-2 (HIGH)** — `_turn_start_ts` never set; `min_mtime` at persist-time breaks jsonl discovery on normal turns and does not guard resume/multi-jsonl.
3. **2A-4 (MEDIUM)** — `validate_cc_acceptance.py` still validates old `{user_id}/` publish paths; no pasted replacement despite commit manifest.
4. **2D-3 (MEDIUM)** — AppLayout keeps two `NextseekApiService` instances; Task 12 does not mandate unification.
5. **2D-2 (MEDIUM)** — Task 12 CC `chatApi` methods named without implementation paste.

---

## Verdict

Iter-10 hardening **partially landed**: `cc_endpoint_guards` and zip relpath test are execution-ready; min_mtime retry text exists but is undermined by missing turn-start assignment and a broken code fence; `validate_cc_acceptance` turn-scoped update remains prose-only.

**Two HIGH findings remain.** UA is not permitted.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
