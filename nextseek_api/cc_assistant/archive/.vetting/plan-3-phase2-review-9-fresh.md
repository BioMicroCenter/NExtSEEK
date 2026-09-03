# PLAN-3 Phase 2 Pre-Execution Review — Iter 9 (Fresh, Cold Context)

**Date:** 2026-06-30  
**Reviewer:** Independent adversarial pre-execution (lenses 2A–2D)  
**Target:** `nextseek_api/cc_assistant/archive/PLAN-3-ui-based-io.md`  
**Locked spec:** `nextseek_api/cc_assistant/archive/SPEC-3-ui-based-io.md`  
**Guide:** `/home/taishajo/AGENTS.md`  
**Method:** Cold read of PLAN + SPEC only; live-code spot-checks on cited anchors (no prior `.vetting/` reads).

---

## Iter-8 Hardening — Verification Against Current PLAN

| Claim | Status | Evidence |
|-------|--------|----------|
| Task 10 paste: `@action` not nested in helpers | **FIXED** | Task 10 Step 1: module-level `_iter_and_cleanup` / `_iter_file`; explicit “class body — **not** nested under helpers” |
| Raw publish: strip `raw/` prefix | **FIXED** | Task 6 `_copy(..., strip_raw_prefix=True)` + `rel.removeprefix("raw/")` |
| `recover_transcript` `cc_session_id` disambiguation | **FIXED** | Task 10 paste: filter when param present; 400 when `qs.count() > 1` and param absent |
| Zip-if-multiple artifacts | **FIXED** | Task 6: `len(art_files) > 1` → single `artifacts.zip` artifact entry |
| Turn-scoped artifact namespace | **FIXED** | Global constraint + Task 6 `output/artifacts/<turn_id>/`, keys `"<turn_id>/<relpath>"`; Task 13 Step 5b |
| Reload CC download (`bundleId === 0`) | **FIXED** | Task 12 Step 5: `(message.bundleId ?? 0) > 0` branch |
| `pytest-cov` in hermetic harness | **PARTIAL** | Global command (L19) includes `--with pytest-cov`; per-task verify commands still omit `--cov-fail-under=95` (see 2A-2) |
| `_newest_jsonl_under` algorithm | **NOT FIXED** | Prose only (Task 11 Step 1); no paste-ready helper or hermetic tests (see 2B-1) |
| Task 10 guard tests | **PARTIAL** | Step 0 names `resolve_artifact_path` + `test_cc_endpoint_guards.py`; no failing-test paste (see 2D-1) |
| Task 6 Step 5c acceptance updates | **PARTIAL** | Step 5c names `validate_cc_acceptance.py`; Step 8 commit omits it (see 2C-2) |

**Live spot-checks (anchors only):** `translate.py:149-156` (no `num_turns`/`duration_ms` yet — expected pre-impl); `cc_engine.py:573-672` (Dropbox + list return); `cc_config.py:15` (laptop default); `cc_provision.py:60-109` (no `input_mnt`); `models_api.py:122-138` (no `cc_traces`); `assistant.py:520-529` (no `cc_traces`/CC `artifacts` passthrough); `useChatApi.ts:27-69` (no sync session-id export); `MessageBubble.tsx:106` (`bundleId!` only); `test_cc_realstack.py:190` (`artifacts_published`); `batch_upload/celery_app.py:50-54` (no upload-task import); `AppLayout.tsx:62` vs `useChatApi` (separate `NextseekApiService` instances); `DROPBOX_DIRECTORY` definition-only at `seek/views.py:94`.

---

## 2A — Vet (correctness, spec alignment, anchor verification)

### Finding 2A-1 — HIGH — `_newest_jsonl_under` still lacks paste-ready, tested selection logic

**Location:** Task 11 Step 1 / persist block (L1562); live `_session_metas` (`cc_assistant.py:91-93`)

**Why defect:** Persist binds the per-turn `CCTrace` and `CCSessionTranscript` blob to “newest `*.jsonl` by mtime” under `cc_state_mnt/projects`. Step 1 names post-turn mtime delta, project-dir constraint, and 3× 200ms retry but supplies **no function body, signature, or hermetic fixture test**. Live `_session_metas` uses the same global-mtime `rglob` pattern — acceptable for 1c memory rollup, **not** for per-turn authoritative storage. On 1b resume or multi-jsonl cc-state trees, the wrong file can be compressed and traced.

**Remedy:** Add paste-ready `_newest_jsonl_under(root, *, min_mtime: float | None = None) -> Path | None` with a hermetic fixture (two jsonls, assert post-turn pick) in Task 11 Step 1 before Step 2 persist wiring.

---

### Finding 2A-2 — MEDIUM — Global ≥95% coverage mandate not wired into per-task verify commands

**Location:** Global Constraints L30 vs Task 1 Step 4, 3 Step 4, 4 Step 9, 5 Step 4, 6 Step 4, 7 Step 5, 9 Step 4, 9b Step 3

**Why defect:** L30 requires `--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95` on each pure-module verify run. L19 adds `pytest-cov` to the harness, but individual task “Run:” blocks still use bare `pytest` (Task 6 marks coverage **optional** only). Executors can satisfy task gates without meeting the global constraint.

**Remedy:** Append the module-specific `--cov` flags to every listed verify command for Tasks 1, 3–7, 9, 9b (not optional on Task 6).

---

### Finding 2A-3 — MEDIUM — Task 10 Step 0 references `resolve_artifact_path` not defined in Step 1 paste

**Location:** Task 10 Step 0 (L1392) vs Step 1 `download_artifact` paste (L1420-1461)

**Why defect:** Step 0 promises a pure helper + parametrize tests; Step 1 inlines path resolution in the DRF action. Cold implementers may skip Step 0 or diverge from the tested seam; owner/key traversal guards remain thin vs SPEC §12.

**Remedy:** Extract `resolve_artifact_path(dirs, key) -> Path` (or equivalent) with paste-ready tests in `test_cc_endpoint_guards.py`; have `download_artifact` call it.

---

### Finding 2A-4 — MEDIUM — SPEC §4 still says `input_src`; plan correctly uses `input_mnt`

**Location:** SPEC-3 §4 L97-98, §12 L383; PLAN Task 9 note L1234

**Why defect:** Locked SPEC prose names host bind `input_src`; Django-in-container must write `input_mnt` (PLAN Task 3/9 is correct). Cross-checking SPEC alone risks writing uploads to an invisible host path.

**Remedy:** Doc-only SPEC amend (`input_mnt` or “mount-visible input path”); PLAN Task 9 note is sufficient for execution if workers follow PLAN.

---

### Finding 2A-5 — LOW — Iter-8 Task 10 nesting / raw-prefix / recover / zip / bundleId items resolved in PLAN text

**Location:** Task 6, 10, 12  
**Status:** Verified fixed in current PLAN (see table above). No new defect.

---

## 2B — Stress, coupling, multi-turn / failure modes

### Finding 2B-1 — HIGH — (Same as 2A-1) Per-turn jsonl binding under-specified for resume / multi-jsonl

**Location:** Task 11  
**Failure mode:** Wrong transcript → wrong `steps`, wrong `CCSessionTranscript` row; panel/DB disagree with on-disk cc-state; Task 13 reload gate may pass with corrupted trace if sentinel still appears in reply.

**Remedy:** Same as 2A-1.

---

### Finding 2B-2 — MEDIUM — `build_artifact_zip` flattens nested deliverables to basename in multi-file zip

**Location:** Task 6 `build_artifact_zip` (L813-828); used when `len(art_files) > 1`

**Why defect:** Zip uses `src.name` only (de-dup by basename). Turn-scoped dirs can hold nested paths (`data/out.csv`, `other/out.csv`); zip loses directory structure and relies on `_1` suffix collision handling. Stress case: same basename in different subdirs → confusing or wrong download content.

**Remedy:** Zip with archive names = `Path.relative_to(art_dir)` (or document flat-zip as intentional and add a hermetic nested-path test).

---

### Finding 2B-3 — MEDIUM — Task 11 document order (11 before 11a) vs blocking constraint

**Location:** Task 11 (L1507) before Task 11a (L1603); Global Constraints L31

**Why defect:** Subagent-per-task execution often walks numeric order. Task 11 Step 1 (kwargs/helpers) can land before `_append_cc_turn_complete` exists; Step 2 is blocked but Step 1 partial wiring still risks early persist call sites in `run_cc_turn` diffs.

**Remedy:** Renumber (11a before 11) or merge into one task with explicit commit boundary after 11a.

---

### Finding 2B-4 — LOW — Upload staging orphans on Celery failure

**Location:** Task 9 staging under `MEDIA_ROOT/cc_upload_staging`  
**Impact:** Disk leak, not functional regression. Acceptable if Task 13 manual cleanup; optional sweeper out of scope.

---

## 2C — Consistency, ordering, documentation coherence

### Finding 2C-1 — MEDIUM — Task 10 Step 4 commit omits guard test file

**Location:** Task 10 Step 4 (L1500-1502)

**Why defect:** Step 0 creates `test_cc_endpoint_guards.py`; commit adds only `services/cc_assistant.py`. Guard tests never land in repo unless executor remembers.

**Remedy:** Add `nextseek_api/cc_assistant/tests/test_cc_endpoint_guards.py` to Step 4 `git add`.

---

### Finding 2C-2 — MEDIUM — Task 6 Step 5c names `validate_cc_acceptance.py`; Step 8 commit omits it

**Location:** Task 6 Step 5c (L920) vs Step 8 commit (L949-952)

**Why defect:** Live `test_cc_realstack.py:190` still reads `artifacts_published`; validator check 16 reads `published_files.json` populated from that field (`validate_cc_acceptance.py:123-130`). Plan says update both; commit manifest only includes `test_cc_realstack.py`. Partial update breaks zero-spend re-validation after Task 6.

**Remedy:** Include `validate_cc_acceptance.py` in Step 8 commit (and grep guard for `artifacts_published` in cc_assistant tests).

---

### Finding 2C-3 — LOW — SPEC §3 flat `output/artifacts/` vs plan turn-scoped namespace

**Location:** SPEC-3 §3 L78-79; PLAN turn-scoped decision L29  
**Status:** User-locked turn-scoped namespace (iter-12). SPEC diagram stale; PLAN is authoritative for execution.

---

### Finding 2C-4 — LOW — File Structure omits several planned test/module paths

**Location:** PLAN File Structure L38-44  
**Missing from Create list:** `cc_upload_list.py`, `test_cc_upload_list.py`, `test_cc_endpoint_guards.py`, `test_cc_chat_log_writer.py`  
**Impact:** Documentation drift only; tasks define them inline.

---

## 2D — Gameability / anti-fake acceptance

### Finding 2D-1 — MEDIUM — Task 10 guard tests described but not specified (no failing-test paste)

**Location:** Task 10 Step 0 (L1390-1392)

**Why defect:** Cheapest pass: skip Step 0, paste endpoints, rely on Task 13 live gate. SPEC §12 calls for owner-scoping at a reachable hermetic seam.

**Remedy:** Paste-ready parametrize tests for traversal rejection + owner filter construction (even if path helper is minimal).

---

### Finding 2D-2 — MEDIUM — Task 9b upload list: helper TDD only, no DRF action fail-first

**Location:** Task 9b Steps 1-2 (L1319-1354)

**Why defect:** Step 2 implements pure helper + DRF action in one step with no “import endpoint → fail” gate. Endpoint can be forgotten while helper tests pass.

**Remedy:** Split: failing grep/import guard for `url_path="upload/list"` before action paste; or source-text guard in Step 3 (plan mentions grep but not fail-first).

---

### Finding 2D-3 — MEDIUM — Task 11 Step 4 “Hermetic regression suite only” without named tests

**Location:** Task 11 Step 4 (L1598)

**Why defect:** No specified tests for persist/jsonl retry/`TurnCompletePayload`. Executor can run existing suite and claim Step 4 done without new regression coverage for Task 11 helpers.

**Remedy:** Name tests (e.g. `test_newest_jsonl_under_*`, `test_turn_complete_payload_shape`) in Step 1/4.

---

### Finding 2D-4 — LOW — Task 4 second fixture adequately anti-overfit

**Location:** Task 4 Step 9b  
**Status:** `cc_transcript_multitool.jsonl` + distinct tool kinds — sufficient.

---

## Spec Coverage (abbreviated)

| Spec slice | Plan tasks | Open review items |
|------------|------------|-------------------|
| §4 upload + list | 3, 9, 9b, 12 | SPEC `input_src` prose (2A-4) |
| §5 hybrid split + download | 6, 10, 12 | Nested zip flatten (2B-2); guards thin (2D-1) |
| §6 activity panel | 4, 5, 7, 11, 11a, 12 | jsonl picker (2A-1/2B-1) |
| §7 transcript DB + recover | 1, 2, 10, 11 | Same jsonl binding risk |
| §8 Dropbox removal | 6+8 | — |
| §9 session id 3e | 12 | Plan addresses dual-service trap |
| §10 security | 9, 10 | Hermetic owner tests (2D-1) |
| §12 testing | 1–12 + 13 | Coverage wiring (2A-2); acceptance validator (2C-2) |

---

## Severity Counts

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 9 |
| LOW | 5 |

*(HIGH finding 2A-1 and 2B-1 are the same root issue counted once in HIGH total.)*

---

## Top Findings (execution order)

1. **2A-1 / 2B-1 (HIGH)** — Paste-ready, tested `_newest_jsonl_under` (post-turn mtime / retry) before Task 11 persist; global-mtime pick is wrong for per-turn DB blobs.
2. **2A-2 (MEDIUM)** — Wire `--cov-fail-under=95` into every pure-module task verify command, not only the global harness line.
3. **2C-2 (MEDIUM)** — Task 6 commit must include `validate_cc_acceptance.py` (and realstack evidence shape) alongside `test_cc_realstack.py`.
4. **2A-3 / 2D-1 (MEDIUM)** — Task 10: define `resolve_artifact_path` + paste-ready guard tests; include test file in commit (2C-1).
5. **2B-3 (MEDIUM)** — Reorder or merge Task 11 / 11a so `_append_cc_turn_complete` lands before any persist wiring.

---

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE** — Iter-8 hardening verified for Task 10 paste structure, raw-prefix strip, recover disambiguation, zip-if-multiple, turn-scoped artifacts, bundleId reload branch, and harness-level `pytest-cov`. One HIGH remains (`_newest_jsonl_under`), plus nine MEDIUM execution-trap and test-wiring gaps. **UA is not granted** while HIGH or MEDIUM findings remain open.
