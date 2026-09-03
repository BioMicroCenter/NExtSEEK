# Independent adversarial pre-execution review — iter 10 (cold context)

**TARGET:** `nextseek_api/cc_assistant/archive/PLAN-3-ui-based-io.md`  
**LOCKED DESIGN:** `nextseek_api/cc_assistant/archive/SPEC-3-ui-based-io.md` (E1–E10, §6.2 enriched trace schema)  
**Reviewer:** Independent cold-context subagent (iter 10); did **not** read prior `.vetting/` findings.  
**Hardening under test:** iter-9 claims (`_newest_jsonl_under`, `cc_endpoint_guards`, zip relpaths, `validate_cc_acceptance` in Task 6 commit, Task 10 download stream fix).

---

## 2A — Vet (permissions & execution snags)

### FINDING 2A-1 — `run_cc_turn` wiring contract incomplete
- **SEVERITY:** HIGH
- **Location:** Task 11 — *"pass `chat_session`, `user_query`, and an `on_turn_complete` callback **into** `run_cc_turn` (new optional kwargs)"*; live call site `services/cc_assistant.py:337-349` passes neither.
- **Why defect:** A cold implementer must invent the `run_cc_turn` signature extension and the `_run` closure capture (`chat_session`, `req.query`, callback) with no paste-ready contract. Wrong placement reintroduces the iter-3/4 failure mode (persist in `_run` where scratch diff / jsonl / `result_meta` are unavailable).
- **Fix:** Paste the exact `run_cc_turn(..., chat_session: ChatSession | None = None, user_query: str = "", on_turn_complete: Callable[[TurnCompletePayload], None] | None = None)` signature and the matching `cc_engine.run_cc_turn(..., chat_session=chat_session, user_query=req.query, on_turn_complete=_append_cc_turn_complete)` call inside `_run`.

### FINDING 2A-2 — Task 10 action snippets missing imports
- **SEVERITY:** MEDIUM
- **Location:** Task 10 Step 1 `download_artifact` — uses `ProjectResolutionError` and `Response`; Task 10 Step 2 `recover_transcript` — returns `Response(..., status=400)` but snippet imports only `HttpResponse, Http404`.
- **Why defect:** Copy-paste implementation fails at import/name resolution before any live gate.
- **Fix:** Add explicit imports in each snippet (`ProjectResolutionError`, `Response` from DRF).

### FINDING 2A-3 — `cc_endpoint_guards` not paste-ready
- **SEVERITY:** MEDIUM
- **Location:** Task 10 Step 0 — *"Create `cc_endpoint_guards.py` with `resolve_artifact_path(artifacts_root: str, key: str) -> Path` — validates `_safe_relpath`, joins under root."*
- **Why defect:** Iter-9 added the module name and a traversal test but no implementation body. Implementers can join paths that escape the root (missing `resolve().is_relative_to()` / commonpath check) or mishandle turn-scoped keys `{turn_id}/file`.
- **Fix:** Paste a complete helper: validate key with `_safe_relpath`, `target = (Path(artifacts_root) / key).resolve()`, reject unless `target.is_relative_to(Path(artifacts_root).resolve())` and `target.is_file()` at call site; add a positive test for `turn1/report.md`.

---

## 2B — Stress test

### FINDING 2B-1 — Task 11 persist block drops `min_mtime` + retry promised in Step 1
- **SEVERITY:** HIGH
- **Location:** Task 11 Step 1 — *"assert `min_mtime` picks the post-turn file … On missing jsonl after **3× 200ms retry**, raise `RuntimeError`"* vs **Minimal persist block** — `jsonl_path = _newest_jsonl_under(Path(dirs.cc_state_mnt) / "projects")` with no `min_mtime`, no retry loop.
- **Why defect:** Under cc-state layouts with multiple `*.jsonl` (resume / project subdirs), newest-by-mtime alone can select a **stale** transcript. Persisted `CCSessionTranscript`, `CCTrace`, and §7 recover endpoint would then serve wrong bytes while Task 13 reload gate appears green.
- **Fix:** Wire `turn_start_ts = time.time()` (or container start monotonic) at turn open; call `_newest_jsonl_under(..., min_mtime=turn_start_ts - 1)` inside the documented 3×200ms retry loop; only then parse/persist.

### FINDING 2B-2 — Task 6 zip unit test contradicts `build_artifact_zip` relpath implementation
- **SEVERITY:** HIGH
- **Location:** Task 6 Step 1 `test_build_zip_contains_all_sources` — *"assert `"a.txt" in names and "b.txt" in names   # basenames, de-duped"`* vs Step 3 `build_artifact_zip` — `arcname = str(src.relative_to(base))` (preserves `sub/b.txt`).
- **Why defect:** Iter-9 hardening explicitly moved to zip **relpaths**, but the RED test still encodes basenames. TDD Step 4 cannot pass without either weakening the implementation (losing nested deliverable paths) or editing the test outside the plan — a hardening regression.
- **Fix:** Update the test to expect `{"a.txt", "sub/b.txt"}` (or pass explicit `arc_prefix=tmp_path` and document turn-scoped zip layout under `art_dir`).

### FINDING 2B-3 — `validate_cc_acceptance` not updated for Step 3 artifact model
- **SEVERITY:** HIGH
- **Location:** Task 6 Step 5c — *"Update `test_cc_realstack.py` + `validate_cc_acceptance.py`"* (commit lists both); live `validate_cc_acceptance.py:121-132` still validates `published_files.json` host-path list with comment *"no run_id nesting"* and `{user_id}/` scoping — incompatible with turn-scoped `output/artifacts/<turn_id>/` and `data["artifacts"]` dict channel.
- **Why defect:** Even after realstack writes `artifacts` instead of `artifacts_published`, the zero-spend validator either **always fails** (blocking Task 6 suite) or passes on the wrong artifact (gameable). Commit instruction includes the file but not the new check semantics.
- **Fix:** Replace `copier_published_scoped` with an artifact check: evidence bundle carries `artifacts.json` (or derived from `forced_result.json`) asserting non-empty `ArtifactFile` entries with turn-scoped keys and on-disk files under `output/artifacts/<turn_id>/`; drop the obsolete `{user_id}/`-only path heuristic.

### FINDING 2B-4 — Global 95% coverage vs Task 6 optional cov
- **SEVERITY:** MEDIUM
- **Location:** Global Constraints — *"Pure modules … require **≥95%** … append `--cov-fail-under=95`"* vs Task 6 Step 5b — *"**Coverage:** optional `--with pytest-cov --cov-fail-under=95`"*
- **Why defect:** Contradictory gates let an implementer skip coverage on `cc_artifacts` while claiming Task 6 complete.
- **Fix:** Make Task 6 Step 5b coverage **required** (match Global Constraints) or document a justified exception in Risk Register with a non-deferrable substitute gate.

### FINDING 2B-5 — Rollback: Task 11 "always re-raise" vs partial deploy
- **SEVERITY:** MEDIUM
- **Location:** Task 11 — *"Failure policy: **Always re-raise** on persist failure until Task 13 live gate passes"*
- **Why defect:** Correct for honesty, but Risk Register rank 1 should explicitly say a failed persist turns a **successful CC turn** into `query_error` / 500 mid-deploy — operators need pause-and-ask before flipping tracker. (Partially noted; coupling to jsonl-missing policy above amplifies blast radius.)
- **Fix:** Add rollback row: if persist raises after container success, block tracker flip; capture failed turn in evidence; do not treat WS reply alone as success.

---

## 2C — Validate external dependencies

### FINDING 2C-1 — Celery registration path is correct but still live-gated
- **SEVERITY:** LOW (informational; no change to locked spec)
- **Location:** Task 9 Step 3b; live `batch_upload/celery_app.py` already routes `cc_assistant.*` and imports `cc_sweep`.
- **Why note:** Plan correctly requires explicit `import nextseek_api.cc_assistant.cc_upload_tasks`. Dependency table marks OK — acceptable. Residual risk is **runtime** registration only provable in Task 13 (already in Risk Register rank 2). No spec conflict.

### FINDING 2C-2 — `zstandard` stream_reader bomb guard
- **SEVERITY:** LOW
- **Location:** Task 1 decompress loop; Dependency Validation marks OK.
- **Verified:** PyPI `zstandard` exposes `ZstdDecompressor.stream_reader`; plan's chunked read + cap matches SPEC §10. No defect.

*(No blocking external-dependency defect found beyond live-gate acceptance already declared.)*

---

## 2D — Gameproof

### FINDING 2D-1 — Task 12 frontend API methods named but not specified
- **SEVERITY:** MEDIUM
- **Location:** Task 12 — *"`uploadFiles`, `pollUpload`, `downloadCcArtifact(sessionId, key)`"* — no paste-ready URLs, poll interval, or error handling; live `chatApi.ts` only has bundle-scoped `downloadArtifact(sessionId, bundleId, artifactKey)`.
- **Why defect:** Cheapest fake: stub methods that `resolve()` without hitting `POST …/cc-assistant/upload/` or `GET …/artifacts/<session>/download/?key=`. Vitest can pass with mocks while Task 13 live gate fails.
- **Fix:** Paste methods mirroring Task 9/10 routes (`/nextseek_api/cc-assistant/upload/`, `upload/status/<job_id>/`, `artifacts/<session>/download/?key=`) and require one Vitest per method asserting the fetch URL path (not mock return values).

### FINDING 2D-2 — Task 12 artifact download prop chain incomplete
- **SEVERITY:** MEDIUM
- **Location:** Task 12 Step 5 — `onCcArtifactDownload?.(key)` vs live `MessageList` / `MessageBubble` — `onArtifactDownload?: (bundleId: number, artifactKey: string) => void` end-to-end.
- **Why defect:** Implementer can patch only `MessageBubble` while `ChatPanel` / `MessageList` still force bundleId through — CC download silently no-ops on reload path.
- **Fix:** Specify prop type union or parallel `onCcArtifactDownload` through `ChatPanel` → `MessageList` → `MessageBubble`, with Vitest on CC-hydrated message (`bundle_id: 0, mode: "cc"`).

### FINDING 2D-3 — Task 11a `serialize_cc_chat_log_entry` helper referenced, not defined
- **SEVERITY:** MEDIUM
- **Location:** Task 11a Step 1 — *"Write failing test asserting `serialize_cc_chat_log_entry(payload)` returns dict with `assistant_reply`, `cc_traces`, `turn_id` keys"*
- **Why defect:** No-op fake: write `assistant_reply` in test only while production code uses `reply`. Grep guard in Step 1 is mentioned but not pasted.
- **Fix:** Paste pure helper + test + grep guard for forbidden `"reply":` key in CC chat_log writer module.

### FINDING 2D-4 — Task 13 live gate is sound (not gameable if enforced)
- **SEVERITY:** LOW (positive)
- **Location:** Task 13 Step 8 — requires `live_gate_transcript.txt` + reload non-empty `cc_traces`.
- **Note:** Correct real-artifact gate per ultraplan 2D doctrine. Prior MEDIUM findings must be fixed so this gate is reachable.

### FINDING 2D-5 — Task 4 second fixture anti-overfit present
- **SEVERITY:** LOW (positive)
- **Location:** Task 4 Step 9b — `cc_transcript_multitool.jsonl` + `test_multitool_trace_kinds()`.
- **Note:** Adequately closes primary fixture overfit called out in Gameability Audit.

---

## Iter-9 hardening verification

| Hardening claim | Present in plan? | Adequate? |
|-----------------|------------------|-----------|
| Paste-ready `_newest_jsonl_under` | Yes (Task 11 Step 1, lines 1608–1615) | **Partial** — helper pasted but **not used** in persist block (2B-1) |
| `cc_endpoint_guards` | Yes (Task 10 Step 0) | **Partial** — test + one-liner spec, no body (2A-3) |
| Zip relpaths | Yes (`build_artifact_zip` uses `relative_to`) | **Contradicted** by Step 1 test expecting basenames (2B-2) |
| `validate_cc_acceptance` in Task 6 commit | Yes (`git add` line 946) | **Incomplete** — file listed but validator logic not updated (2B-3) |
| Task 10 download fix (`_iter_file` vs `_iter_and_cleanup`) | Yes (Task 10 note line 1470) | **OK** |

---

## Non-blocking cosmetic notes

- File Structure (line 58) lists `lib/api/chatApi.ts`; canonical path is `lib/services/chatApi.ts` (Task 12 uses correct path).
- Task 11 section appears before Task 11a in the document; load-bearing order note exists but numbering remains confusing.
- Task 13 Step 1 re-adds `zstandard` already mandated in Task 1 Step 5 (harmless redundancy).
- Phase 2 Vetting Log iteration numbering (iter 10 row labeled "iter 6" in historical table) is editorial only.

---

## Summary

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 4 |
| MEDIUM | 8 |
| LOW | 5 (+2 positive notes) |

**Top defects:** (1) Task 11 persist block omits `min_mtime`/retry despite iter-9 helper; (2) zip test vs relpath implementation mismatch; (3) `validate_cc_acceptance` not redesigned for turn-scoped artifacts; (4) `run_cc_turn` callback wiring unpasted.

**Spec alignment:** Findings are plan-side gaps; none require amending locked E1–E10 or §6.2.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
