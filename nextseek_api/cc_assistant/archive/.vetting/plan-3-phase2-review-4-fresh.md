# PLAN-3 Phase 2 Review 4 — Fresh Independent (2026-06-30)

**Target:** `/home/taishajo/work/NExtSEEK/nextseek_api/cc_assistant/PLAN-3-ui-based-io.md`  
**Spec of record:** `SPEC-3-ui-based-io.md`  
**Method:** Cold context only — plan + spec + live codebase spot-checks. No `.vetting/` inputs.

---

## Phase 2 Section Presence (2A gate)

| Section | Present | Location |
|---------|---------|----------|
| Permissions Required | Yes | L1671–1685 |
| Risk Register | Yes | L1691–1704 |
| Dependency Validation | Yes | L1708–1718 |
| Gameability Audit | Yes | L1722–1734 |
| Phase 2 Vetting Log | Yes | L1738–1748 |

Phase 2 structural completeness: **PASS**.

---

## 2A — Vet (execution readiness + permissions)

### Finding 2A-1 — HIGH — Task 12 AppLayout 3e uses wrong service instance for authoritative session id

**Confidence:** 95  
**Location:** PLAN-3 Task 12 Step 1 (L1513–1526); `chat_frontend/src/AppLayout.tsx`; `chat_frontend/src/hooks/useChatApi.ts`

**Why defect:** Task 12 instructs AppLayout to promote from `service.sessionId`, but AppLayout submits queries via `useChatApi().submitQuery`, which owns a **separate** `NextseekApiService` instance (`useChatApi.ts:28–29`). AppLayout’s `const [service] = useState(() => new NextseekApiService(...))` (`AppLayout.tsx:62`) is only passed to `useSessions` — it never receives the HTTP-202 `session_id`. The authoritative id is set on the hook’s internal `serviceRef` at `chatApi.ts:96` before WS events fire. Using `service.sessionId` in AppLayout’s `query_complete` handler reads the wrong object (likely always `null`), so 3e defense-in-depth fails on the AppLayout route even if EmbeddedApp is fixed.

**Fix:** In AppLayout, promote from the same instance that submitted — e.g. expose `getAuthoritativeSessionId()` from `useChatApi` (reading `serviceRef.current.sessionId`), or unify to one shared `NextseekApiService` ref for both submit and sessions. Do not use the standalone `service` state object for 3e.

---

### Finding 2A-2 — MEDIUM — Task 12 cites non-existent `lib/api/chatApi.ts`

**Confidence:** 95  
**Location:** PLAN-3 File Structure (L56); Task 12 Files (L1504)

**Why defect:** Repo path is `chat_frontend/src/lib/services/chatApi.ts` (`NextseekApiService`). Plan lists `lib/api/chatApi.ts` twice. Cold implementer following the file list will edit or create the wrong path.

**Fix:** Replace all `lib/api/chatApi.ts` references with `lib/services/chatApi.ts`.

---

### Finding 2A-3 — MEDIUM — Task 6 omits hermetic test updates for `_publish_artifacts` return-shape change

**Confidence:** 92  
**Location:** PLAN-3 Task 6 (L730–927); `nextseek_api/cc_assistant/tests/test_cc_engine_publish.py`

**Why defect:** Task 6 reworks `_publish_artifacts` from `list[str]` to a structured `dict`, and Step 7 asserts the full hermetic suite passes. `test_cc_engine_publish.py` still asserts list equality against the return value (`L22–23`, `L42`). That test is in the default hermetic path and will fail until updated. Plan neither lists the file under **Modify** nor adds a step to rewrite assertions (e.g. expect `result["artifacts"]`, host paths under `output/artifacts/`).

**Fix:** Add `test_cc_engine_publish.py` to Task 6 **Modify** with explicit assertion updates before Step 7’s full-suite gate.

---

### Finding 2A-4 — MEDIUM — Task 9 upload action snippet missing `import os` / `import time`

**Confidence:** 90  
**Location:** PLAN-3 Task 9 Step 5 (L1207–1262); `nextseek_api/services/cc_assistant.py` (imports L20–59)

**Why defect:** Pasted `upload()` uses `os.path.join`, `os.makedirs`, and `time.time()` but `cc_assistant.py` currently imports neither `os` nor `time` (only `logging`, `threading`, DRF, etc.). Copy-paste without import additions → `NameError` at first upload.

**Fix:** Add explicit Step 5 preamble: `import os` and `import time` at module top (mirror `batch_upload/views.py:5–7`).

---

### Finding 2A-5 — MEDIUM — Task 10 `download_artifact` lacks `ProjectResolutionError` handling present on upload

**Confidence:** 85  
**Location:** PLAN-3 Task 10 Step 1 (L1312–1348) vs Task 9 Step 5 (L1226–1230)

**Why defect:** Upload wraps `resolve_user_project` in try/except and returns 503. Download calls `resolve_user_project` bare — resolution failure becomes an unhandled 500 instead of a controlled error, inconsistent with §10 fail-closed posture and upload behavior.

**Fix:** Mirror upload’s `try/except ProjectResolutionError → 503` (or 404) in `download_artifact`.

---

**Permissions catalogue:** `## Permissions Required` (L1671–1685) matches task needs (hermetic pytest, Celery broker, MEDIA_ROOT, DMAC mounts, ORM migrate, Docker, Playwright). No missing permission classes identified.

**Prior iter fixes verified in plan text:** Task 11 `turn_id == str(run_id)` + `update_or_create` keys (L1410–1417); `assistant_reply` chat_log contract (L1406, L1489–1490); persist site in `cc_engine.run_cc_turn` (L1391–1468); Task 12 CC artifact download via parent session id (L1576–1577); Celery explicit import Step 3b (L1190–1196); Tasks 6+8 atomic coupling (L915, L1006–1013).

---

## 2B — Stress Test

### Finding 2B-1 — MEDIUM — Task 11a `_append_cc_turn_complete` under-specified vs Risk Register #1

**Confidence:** 88  
**Location:** PLAN-3 Task 11a (L1479–1495); Risk Register (L1695)

**Why defect:** Risk Register ranks 11/11a as #1 (“reload empty / users trust panel data that vanishes”). Task 11a defines the interface and ordering but provides no paste-ready implementation, no failing hermetic test (the mentioned grep guard has no test body), and no verify command. Compared to Tasks 1–7 (full TDD steps), the highest-risk reload path is the thinnest execution contract.

**Fix:** Add paste-ready `_append_cc_turn_complete` (canonical RMW + `chat_log` append + `es["cc_traces"]` mirror + transcript upsert), a grep/source guard test for `assistant_reply` key, and a verify step before Task 11 Step 2 commit.

---

### Finding 2B-2 — MEDIUM — Empty/missing jsonl skips all persist with no failure signal

**Confidence:** 82  
**Location:** PLAN-3 Task 11 minimal block (L1443–1468)

**Why defect:** Persist runs only `if trace is not None`, and `trace` is None when `raw` is empty or `parsed` is falsy. A successful CC turn could emit live `query_complete` with artifacts but write no `chat_log` / `CCSessionTranscript` row. Task 13 Step 6 reload gate would fail opaquely; implementer may treat as “works live, reload flake.”

**Fix:** Document explicit policy: either (a) treat missing jsonl as hard error (re-raise / `query_error`) per Task 11 failure policy, or (b) still append a minimal `chat_log` entry with empty `cc_traces` and log a warning — but do not silently skip persist on success path.

---

### Finding 2B-3 — LOW — Cumulative session jsonl stored per turn (no slice)

**Confidence:** 75 (below reporting threshold)

Each turn upserts the newest full session jsonl blob, not a turn slice. Wasteful but recoverable; SPEC §7 allows full jsonl per `(session, turn)`.

---

## 2C — Validate External Dependencies

| Dependency | Plan claim | Verification | Status |
|------------|------------|--------------|--------|
| `zstandard` | Add ≥0.25 Task 1 | Not in `pyproject.toml` today (expected pre-impl) | OK — Task 1 + Task 13 |
| pydantic v2 unpinned + ordered `Union` | `_Other` last | `cc_summary.py` uses unpinned pydantic; plan `_Other.type: str \| None` tolerates `{"_type":"unparsed"}` from `parse_transcript` | OK |
| Celery `batch_upload.celery_app` | Explicit import Step 3b | Live `celery_app.py:54` pattern matches; route `cc_assistant.*` at L37 | OK |
| Vitest / `build:embedded` | Task 12/13 | `chat_frontend/package.json` L9–12 | OK |
| Migration 0007 deps | `0006_merge_extra_state_guards` | File exists | OK |
| `CCStreamTranslator._handle_result` | Task 5 target | Confirmed `translate.py:26`, `:130–156` | OK |
| `input_mnt` vs SPEC `input_src` | Plan Task 3 | Django runs in container; `input_mnt` is correct (SPEC §4 prose cites host path; plan aligns with Step-2 `*_mnt` convention) | OK — intentional |

No blocking external dependency failures.

---

## 2D — Gameproof

### Finding 2D-1 — MEDIUM — ≥95% coverage global constraint not wired into task verify commands

**Confidence:** 90  
**Location:** Global Constraints (L29); Tasks 1–7 verify steps

**Why defect:** Global Constraints require `--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95` on each pure-module verify command. Individual task verify lines (e.g. Task 1 L158, Task 4 L625) run bare `pytest -q` only. Implementer can satisfy every checkbox while violating the hardened coverage claim.

**Fix:** Append `--cov=... --cov-fail-under=95` to each listed pure-module verify command (Tasks 1, 3–7, 9 validator).

---

### Finding 2D-2 — MEDIUM — Task 9b remains outline-only (upload list)

**Confidence:** 88  
**Location:** PLAN-3 Task 9b (L1278–1294); Gameability Audit (L1728)

**Why defect:** SPEC §4 requires “upload + list.” Task 9b has bullet placeholders only — no failing test snippet, no verify command, no paste-ready DRF action. Cheapest fake: empty `{"files":[]}` endpoint; Task 13 Step 4 gate is the only oracle.

**Fix:** Promote to full TDD task: paste failing test for `list_input_files`, verify command, commit step, and explicit Task 13 Step 4 assertion text.

---

### Finding 2D-3 — LOW — Task 4 second fixture in Step 9b but commit in Step 10

**Confidence:** 70 (below threshold)

Task 4 Step 9b + Step 10 commit includes multitool fixture. Acceptable if implementer follows steps in order.

---

## Summary

| Severity | Count | IDs |
|----------|-------|-----|
| CRITICAL | 0 | — |
| HIGH | 1 | 2A-1 |
| MEDIUM | 8 | 2A-2, 2A-3, 2A-4, 2A-5, 2B-1, 2B-2, 2D-1, 2D-2 |
| LOW | 2 | 2B-3, 2D-3 |

---

## Required fixes before UNCONDITIONAL_ACCEPTANCE

1. **2A-1 (HIGH)** — Fix AppLayout 3e to read authoritative session id from the submit `NextseekApiService` instance (via `useChatApi`), not the separate `service` state used for sessions.
2. **2A-2 (MEDIUM)** — Correct `chatApi.ts` path to `lib/services/chatApi.ts`.
3. **2A-3 (MEDIUM)** — Task 6 must update `test_cc_engine_publish.py` for dict return shape before full-suite gate.
4. **2A-4 (MEDIUM)** — Task 9 must add `import os` / `import time` to `cc_assistant.py`.
5. **2A-5 (MEDIUM)** — Task 10 download: handle `ProjectResolutionError`.
6. **2B-1 (MEDIUM)** — Flesh out Task 11a with paste-ready persist helper + grep guard test + verify.
7. **2B-2 (MEDIUM)** — Define non-silent policy when jsonl/trace is missing on success.
8. **2D-1 (MEDIUM)** — Wire `--cov-fail-under=95` into pure-module verify commands.
9. **2D-2 (MEDIUM)** — Promote Task 9b from outline to full TDD steps.

---

## Positive notes (no MEDIUM+)

- Spec coverage self-review (L1651–1663) accurately maps §4–§14 to tasks including Task 9b list and 11/11a reload seam.
- Tasks 6+8 atomic Dropbox removal + hybrid split coupling is explicit.
- Task 11 transcript recover contract (`turn_id == str(run_id)`, `cc_session_id`, `update_or_create` keys) is now specified.
- Celery worker registration Step 3b addresses prior NotRegistered failure mode.
- Task 4 anti-overfit second fixture is present in steps and commit list.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
