# PLAN-3-ui-based-io.md — independent adversarial pre-execution review (iter-12, cold context)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-3-ui-based-io.md`  
**Locked design:** `nextseek_api/cc_assistant/archive/SPEC-3-ui-based-io.md`  
**Iter-11 hardening acknowledged (spot-checked in plan text only):** persist code fence closed; `_turn_start_ts` prose at turn open; `validate_cc_acceptance` paste block present; Task 12 Step 0 AppLayout single-service ref via `useChatApi`.

**Live spot-checks (claim verification only, not prior vetting files):** `Turn.bundle_id: int` required (`models_api.py:131`); `test_cc_realstack.py:190` still emits `artifacts_published`; `validate_cc_acceptance.py:121-130` still `{user_id}/` heuristic; `AppLayout.tsx:62-64` dual `NextseekApiService`; `useChatApi.ts` lacks sync session export; `cc_engine.py:573-588` Dropbox + list return; canonical `chatApi.ts` at `lib/services/chatApi.ts`.

---

## 2A — Vet

### Finding 2A-1 — CRITICAL — Task 7 projection passes `bundle_id=None` into `Turn` where `bundle_id: int` is required

**Location:** Task 7 Step 4 — *"`bundle_id=bid if bid is not None else (None if entry.get("mode") == "cc" else 0)`"*

**Why defect:** Live `Turn` declares `bundle_id: int` (not `Optional[int]`). CC `chat_log` entries have no `bundle_id`; the paste passes `None`. `get_session` constructing `Turn(...)` will raise `pydantic.ValidationError`, breaking session reload for every CC turn after Task 11a writes `chat_log` — directly violating SPEC §6.5 / §12 reload gate. Task 7 tests only construct `Turn(bundle_id=0, …)` and never exercise this projection branch, so the defect survives hermetic runs.

**Fix:** Keep `bundle_id=0` for CC turns (consistent with Task 12 Step 5 `(message.bundleId ?? 0) > 0` native branch). Remove the `None if mode=="cc"` branch from the paste. Add a hermetic projection test: CC `chat_log` entry without `bundle_id` → serialized turn has `bundle_id: 0` and `cc_traces` populated.

---

### Finding 2A-2 — HIGH — Task 6 Step 5c title promises `test_cc_realstack.py` update but supplies no realstack paste

**Location:** Task 6 Step 5c — *"Update `test_cc_realstack.py` + `validate_cc_acceptance.py`"* vs paste ending at `artifacts_turn_scoped` only

**Why defect:** Live realstack still writes `artifacts_published` and `published_files.json` with host-path strings (`test_cc_realstack.py:190-191`). Task 6 removes that field and returns `artifacts` dicts. Without explicit realstack edits (capture `data["artifacts"]`, turn-scoped keys, update `forced_result.json` shape), hermetic realstack fails and acceptance check 16 stays wired to the obsolete `{user_id}/` heuristic even if a new helper is pasted elsewhere.

**Fix:** Add paste-ready realstack diff: replace `artifacts_published` capture with `artifacts`; write turn-scoped keys into evidence; replace check #16 body **and** its `add("copier_published_scoped", …)` call to invoke `artifacts_turn_scoped`. Include both files in Step 8 `git add`.

---

### Finding 2A-3 — HIGH — Task 10 endpoint pastes omit required imports (`Response`, `ProjectResolutionError`)

**Location:** Task 10 Step 1 `download_artifact` paste — *"`except ProjectResolutionError: return Response({...}, status=503)`"*; Step 2 `recover_transcript` — *"`return Response({"error": "cc_session_id required"}, status=400)`"*

**Why defect:** The pasted action bodies reference `ProjectResolutionError` and `Response` but the surrounding import block lists only `StreamingHttpResponse, Http404` (download) or `HttpResponse, Http404` (recover). A literal paste yields `NameError` at import/compile time before Task 13 live gate.

**Fix:** Add to each paste's import stanza: `from rest_framework.response import Response` and `from nextseek_api.cc_assistant.cc_provision import ProjectResolutionError` (or module-level imports in `cc_assistant.py` per Task 9 Step 5 note).

---

### Finding 2A-4 — MEDIUM — File Structure still lists wrong frontend API path

**Location:** File Structure (Modify) — *"`lib/api/chatApi.ts`"*

**Why defect:** Canonical path is `chat_frontend/src/lib/services/chatApi.ts` (Task 12 body and live tree). Cold implementer following the file list may create or edit the wrong path.

**Fix:** Replace with `lib/services/chatApi.ts` (and align Task 12's `chatApi.ts:96` line reference).

---

### Finding 2A-5 — MEDIUM — Task 11 `_turn_start_ts` wiring not owned by any numbered step

**Location:** Task 11 — prose *"At turn start (before container spawn): `translator._turn_start_ts = time.time()`"* sits above the persist fence; Step 1 says *"Helpers only … do not call `on_turn_complete` yet"* without assigning `_turn_start_ts`; Step 2 says *"Implement persist block"* only.

**Why defect:** Iter-11 hardening added the prose but not a checklist item at the correct insertion point in `run_cc_turn` (before `containers.run`, ~`:499`). Implementer completing Step 2 without reading prose hits `AttributeError` on `_turn_start_ts` or mis-places `time.time()` at persist time (defeating `min_mtime` disambiguation on resume/multi-jsonl trees).

**Fix:** Add explicit Step 1 sub-step: *"In `run_cc_turn`, immediately after `translator = CCStreamTranslator()`, set `translator._turn_start_ts = time.time()` before container spawn."* Add hermetic test or source grep guard.

---

## 2B — Stress Test

**Most likely failure:** Task 7 projection `ValidationError` on first CC reload after Task 11a — activity panel and artifact download appear live (WS emit) but session fetch 500/empty turns on reload; Task 13 Step 6 fails late.

**Most catastrophic failure:** Task 10 import `NameError` merged without live HTTP smoke — artifact/transcript endpoints unreachable in production while tracker flipped on green hermetic unit tests.

**Hidden dependencies:** Task 6 → Task 8 grep guard (`artifacts_published` removal); Task 11a → Task 11 Step 2 ordering; Celery worker import (`batch_upload/celery_app.py`) → Task 13 Step 4 upload; embedded frontend rebuild → Task 12 before Task 13 Step 2.

**Ambiguous success — empty jsonl:** Task 11 persist uses `if parsed else None`; `parse_transcript(b"")` returns a truthy empty `ParsedTranscript`, so a zero-byte jsonl file satisfies persist without raising `RuntimeError("cc persist: missing transcript jsonl…")`. Task 13 reload could show empty `cc_traces` while gate prose claims hard fail.

**Coverage risk:** Global Constraints mandate `≥95%` + `--cov-fail-under=95` for Tasks 1, 3–7, 9, 9b, but Task 1/3/4/5/7 verify commands omit coverage flags; Task 6 marks coverage *optional*. Implementer can skip coverage floor while claiming Phase 2 compliance.

**Rollback:** Task 6+8 atomic handler edit — partial revert leaves Dropbox string + dict/list mismatch; pause before deploy if Task 7/11a reload path not proven.

---

## 2C — Validate External Dependencies

| Dependency | Status | Note |
|------------|--------|------|
| `zstandard>=0.25` stream decompress | OK | Task 1 `stream_reader` + chunked cap matches python-zstandard 0.25 docs |
| pydantic v2 ordered `Union` | OK | Plan mitigates unpinned pydantic per SPEC §6.3 |
| Celery `batch_upload` queue + explicit import | OK | Task 9 Step 3b + Task 13 grep verify |
| Vitest / `npm run build:embedded` | OK | Referenced in Task 12/13 |
| Django migration `0007` after `0006_merge_extra_state_guards` | OK | Live tree has `0006_merge_extra_state_guards.py` |
| Playwright live gate | Accepted | Task 13 only; documented exception |

No blocking external API mismatch found for zstandard/pydantic/Celery beyond the in-plan mitigations.

---

## 2D — Gameproof

### Finding 2D-1 — MEDIUM — Global 95% coverage gate is gameable (verify commands don't enforce it)

**Success condition (as written):** Global Constraints — *"Pure modules (Tasks 1, 3–7, 9 validator, 9b) require ≥95% line coverage — append `--cov-fail-under=95` to each task's verify command"*

**Cheapest fake:** Run only the quoted `pytest -q` lines without `--cov`; Task 6 explicitly labels coverage *optional*.

**Remedy:** Make `--with pytest-cov --cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95` mandatory in every listed task's Step "Run tests" command (remove "optional" on Task 6 Step 5b).

---

### Finding 2D-2 — MEDIUM — Task 12 CC I/O API methods named but not paste-ready

**Success condition:** Task 12 — *"`uploadFiles`, `pollUpload`, `downloadCcArtifact(sessionId, key)`"*

**Cheapest fake:** Stub methods returning resolved promises; Vitest mocks the stub; live Task 13 fails on wrong URLs/poll interval.

**Remedy:** Paste minimal `NextseekApiService` methods with exact paths (`POST …/cc-assistant/upload/`, `GET …/upload/status/<job_id>/`, `GET …/artifacts/<session>/download/?key=`), poll backoff, and error propagation; colocated unit test hits URL construction without network.

---

### Finding 2D-3 — MEDIUM — Task 11 empty-jsonl bypasses hard-fail policy

**Success condition:** *"If `query_complete` fires but no jsonl is found … re-raise `RuntimeError`"*

**Cheapest fake:** Touch an empty `*.jsonl` under cc-state so `jsonl_path` is truthy, `raw=b""`, `parsed` truthy → persist empty trace + empty blob; Task 13 Step 6 prose check passes if agent only verifies non-null `cc_traces` array.

**Remedy:** Treat `not raw.strip()` or `parsed.line_count == 0` after successful CC turn as the same `RuntimeError`; assert `transcript_line_count > 0` in Task 13 Step 6 saved output.

---

### Finding 2D-4 — LOW — Task 13 Step 9 references SPEC-7 / PLAN-7 (wrong step)

**Location:** Task 13 Step 9 — *"Hard gate for Step 7: this file must be committed … (SPEC-7 §8 / PLAN-7 Task 1)"*

**Why defect:** Copy-paste from Step 7 deploy plan; Step 3 evidence commit gate is correct in prose above but cross-reference sends implementer to wrong spec.

**Fix:** Replace with SPEC-3 §12 / PLAN-3 Task 13 Step 8–9 evidence requirements only.

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log table references prior `.vetting/` filenames — expected orchestrator metadata; not a execution blocker.
- SPEC §3 diagram mentions `.claude/projects`; live cc-state code uses `projects/` directly (`cc_session.py:53`) — plan correctly follows live `_session_metas` path, not the spec diagram prose.
- Task 11 interface list duplicates transcript upsert description (callback payload vs inline ORM snippet) — redundant but not contradictory if `_append_cc_turn_complete` is the single writer.

---

## Summary

| Severity | Count |
|----------|------:|
| CRITICAL | 1 |
| HIGH | 2 |
| MEDIUM | 6 |
| LOW | 1 |

**Top findings:** (1) Task 7 `bundle_id=None` vs required `int` breaks CC reload. (2) Task 6 Step 5c missing realstack/acceptance wiring paste. (3) Task 10 endpoint pastes missing imports. (4) Coverage floor declared globally but not enforced in verify commands. (5) Task 11 empty-jsonl gameable persist.

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE**
