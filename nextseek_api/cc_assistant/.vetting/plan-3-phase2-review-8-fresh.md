# PLAN-3 Phase 2 Pre-Execution Review — Iter 8 (Fresh, Cold Context)

**Date:** 2026-06-30  
**Reviewer:** Independent adversarial (cold context only)  
**Target:** `PLAN-3-ui-based-io.md`  
**Locked spec:** `SPEC-3-ui-based-io.md`  
**Guide:** `/home/taishajo/AGENTS.md`  
**Iter-7 hardening claimed:** zip-if-multiple publish, bundleId reload fix (mode/cc), pytest-cov in harness, Task 10 guard helpers, realstack artifacts update, jsonl retry  

**Live spot-checks (anchors only):** `translate.py:130-156` (no `num_turns`/`duration_ms` yet), `cc_engine.py:573-672` (Dropbox + list return), `cc_config.py:15` (laptop default), `cc_provision.py:60-107` (no `input_mnt`), `models_api.py:122-138` (no `cc_traces`), `assistant.py:521-529` (no `cc_traces`/CC artifacts passthrough), `useChatApi.ts` (no sync session id export), `MessageBubble.tsx:106` (`bundleId!` only), `test_cc_realstack.py:190` (`artifacts_published`), `batch_upload/celery_app.py:50-54` (no upload task import yet).

---

## Iter-7 Hardening Verification

| Claim | Status | Evidence |
|-------|--------|----------|
| zip-if-multiple in publish | **FIXED in plan** | Task 6 Step 5: `len(art_files) > 1` → `build_artifact_zip` + single `artifacts.zip` key |
| bundleId reload fix | **FIXED in plan** | Task 12 Step 5: `(message.bundleId ?? 0) > 0` + mode/cc branch |
| pytest-cov in harness | **FIXED in plan** | Global Constraints L19: `--with pytest-cov` in hermetic command |
| Task 10 guard helpers | **PARTIAL** | Step 0 names helpers/tests; no paste-ready tests; paste still broken (2A-1) |
| realstack artifacts update | **FIXED in plan text** | Task 6 Step 5c; commit scope gap (2C-3) |
| jsonl retry | **FIXED in plan text** | Task 11 Step 1: 3× 200ms retry; no algorithm snippet (2B-1) |

---

## 2A — Vet (correctness, spec alignment, anchor verification)

### Finding 2A-1 — HIGH — Task 10 paste nests `download_artifact` inside `_iter_file`

**Location:** Task 10 Step 1 paste (~L1409–1456)

**Why defect:** The `@action` decorator and entire `download_artifact` method are indented under `_iter_file`, making them a nested function never registered on `CCAssistantViewSet`. A literal paste yields no working download route.

**Remedy:** Dedent `_iter_and_cleanup` / `_iter_file` to module level (or a helpers module); keep `@action` methods at class body indent. Add a grep/source guard: `def download_artifact` must not appear nested under `def _iter_file`.

---

### Finding 2A-2 — HIGH — Raw publish path double-`raw/` prefix

**Location:** Task 6 Step 5 `_copy` + `partition_changed` (~L863–883)

**Why defect:** `partition_changed` keeps scratch relpaths like `raw/debug.log`. `_copy` writes to `output_mount / "raw" / rel`, producing `output/raw/raw/debug.log`. SPEC-3 §3 target layout shows deliverables under `output/raw/` mirroring scratch content, not `output/raw/raw/…`. Live `_publish_artifacts` today copies flat under `output_mount / rel` (no extra prefix).

**Remedy:** When copying raw rels, strip leading `raw/` (or map `raw/x` → `output/raw/x`). Add hermetic test: changed set `{"raw/debug.log"}` → host file at `…/output/raw/debug.log`, not `…/output/raw/raw/debug.log`.

---

### Finding 2A-3 — MEDIUM — `recover_transcript` interface vs paste vs Task 13 gate disagree on `cc_session_id`

**Location:** Task 10 Interfaces L1384; Step 2 paste L1462–1481; Task 13 Step 5 L1756

**Why defect:** Interface states `cc_session_id` query param is **required** when multiple rows match `(chat_session, turn_id)`. Paste filters only `(chat_session, turn_id)` with `.order_by("-created_at").first()` and treats disambiguation as optional comment. Task 13 live gate expects `GET …/transcript/<session>/<turn>/?cc_session_id=…`.

**Remedy:** Filter ORM with `cc_session_id` when param present; 400 if multiple rows and param absent. Align Task 13 gate wording with enforced behavior.

---

### Finding 2A-4 — MEDIUM — Global ≥95% coverage mandate not wired into task verify commands

**Location:** Global Constraints L30–31 vs Task 1 Step 4, Task 3 Step 4, Task 6 Step 5b (“optional” cov)

**Why defect:** Global constraint requires `--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95` on each pure-module verify run. Most task verify blocks omit `--cov`; Task 6 explicitly marks coverage optional. Subagent-driven execution can skip the hardened gate.

**Remedy:** Append the cov flags to every listed verify command for Tasks 1, 3–7, 9 validator, 9b; remove “optional” wording in Task 6 Step 5b.

---

### Finding 2A-5 — MEDIUM — Task 10 Step 0 references `resolve_artifact_path` not defined in Step 1 paste

**Location:** Task 10 Step 0 L1390 vs Step 1 paste (inline path logic only)

**Why defect:** Step 0 promises parametrize tests for `resolve_artifact_path(dirs, key)` traversal rejection, but Step 1 inlines resolution inside `download_artifact` with no extractable pure helper. TDD Step 0 cannot run before implementation shape exists.

**Remedy:** Extract `resolve_artifact_path(output_mnt, key) -> Path` (or reject) as a pure function in `cc_artifacts.py` or `cc_upload_tasks.py`; test it in `test_cc_endpoint_guards.py`; call from the action.

---

### Finding 2A-6 — LOW — SPEC §4 still says `input_src`; plan correctly uses `input_mnt`

**Location:** SPEC-3 §4 L97–98; PLAN Task 9 L1232 note

**Why defect:** Spec prose is stale (Django in-container writes mount paths). Plan is correct; implementers reading spec first may write to wrong path.

**Remedy:** Amend SPEC §4 destination bullet to `input_mnt` (or “mount-visible input path”) — doc-only, no plan change required for execution if workers follow PLAN.

---

## 2B — Stress, coupling, multi-turn / failure modes

### Finding 2B-1 — HIGH — `_newest_jsonl_under` selection underspecified for resume / multi-jsonl stores

**Location:** Task 11 Step 1; live `_session_metas` uses global mtime `rglob("*.jsonl")` (`cc_assistant.py:92–93`)

**Why defect:** cc-state holds `projects/**/*.jsonl`; resume accumulates multiple files. “Newest by mtime + post-turn delta + 3× 200ms retry” is load-bearing for trace/transcript persist but has no paste-ready helper, no hermetic test, and no project-dir / session scoping rule. Wrong jsonl → wrong `CCTrace`, wrong DB blob, reload shows incorrect activity.

**Remedy:** Add `_newest_jsonl_under(root: Path, *, min_mtime: float | None) -> Path | None` with tests (fixture tree, two jsonls, pick post-turn file). Scope search to current cc-state session dir only.

---

### Finding 2B-2 — MEDIUM — Task 11 fail-loud on missing jsonl may flake before retry window exhausted

**Location:** Task 11 Step 1 / empty-jsonl policy L1584–1585

**Why defect:** CC container teardown + filesystem sync may lag; single read after turn without bounded retry races slow hosts. Plan mentions retry in Step 1 prose but persist block (Step 2 paste) has no retry loop.

**Remedy:** Include retry loop in paste-ready persist block; only raise after retries exhausted; log each miss.

---

### Finding 2B-3 — MEDIUM — Tasks 6 Step 5c and Step 8 commit granularity

**Location:** Task 6 Steps 5c vs 8 L947–951

**Why defect:** Step 5c requires updating `test_cc_realstack.py` (live: still asserts `artifacts_published` at L190). Step 8 `git add` omits realstack/acceptance files. Subagent per task can land hybrid split without updating the spend gate test → false red or skipped gate.

**Remedy:** Include `test_cc_realstack.py` and `validate_cc_acceptance.py` (if needed) in Task 6 Step 8 commit manifest; add grep guard for `artifacts_published` in cc_assistant tests.

---

### Finding 2B-4 — LOW — Upload staging orphans on Celery failure

**Location:** Task 9 Step 5 staging under `MEDIA_ROOT/cc_upload_staging`

**Why defect:** Temp files remain if task never runs or worker dies. Not Step-3 blocker; operational debt.

**Remedy:** Note cleanup in task `on_failure` or periodic sweep — out-of-scope acceptable if documented.

---

## 2C — Consistency, ordering, documentation coherence

### Finding 2C-1 — MEDIUM — Task document order (11 before 11a) vs blocking constraint

**Location:** Global Constraints L31; Task 11 header before Task 11a in file

**Why defect:** File order still presents Task 11 before 11a. Global constraint and Task 11 banner say 11a MUST commit before Task 11 Step 2, but skimming task sequence invites wrong execution order.

**Remedy:** Physically reorder Task 11a before Task 11 in the plan, or add an explicit “STOP — complete Task 11a first” gate at Task 11 Step 2 with no earlier persist steps.

---

### Finding 2C-2 — MEDIUM — Task 10 Step 4 commit omits guard test file

**Location:** Task 10 Step 4 L1491–1493

**Why defect:** Step 0–3 introduce `test_cc_endpoint_guards.py`; commit only adds `cc_assistant.py`. Guard tests can be dropped.

**Remedy:** `git add …/test_cc_endpoint_guards.py` in Step 4 commit.

---

### Finding 2C-3 — LOW — SPEC §3 flat `output/artifacts/` vs plan turn-scoped namespace

**Location:** SPEC-3 §3 L78–79 vs PLAN Global Constraints L29

**Why defect:** Spec diagram does not show `<turn_id>/` subdirs; plan is authoritative (user decision 2026-06-30). Low risk if implementers follow PLAN only.

**Remedy:** Update SPEC §3 diagram to `artifacts/<turn_id>/` for coherence.

---

## 2D — Gameability / anti-fake acceptance

### Finding 2D-1 — MEDIUM — Task 10 guard tests described but not specified

**Location:** Task 10 Step 0 L1388–1390

**Why defect:** No failing test paste; executor can add empty `test_cc_endpoint_guards.py` or skip. SPEC §12 calls for owner-scoping hermetic tests at reachable seams.

**Remedy:** Paste minimal parametrize tests (traversal keys, session-id format) and require RED before endpoint paste.

---

### Finding 2D-2 — MEDIUM — Task 9b upload list: helper TDD only, no DRF action fail-first

**Location:** Task 9b Steps 1–3

**Why defect:** Only `list_input_files` gets a failing test. `@action upload_list` can ship untested until Task 13. Cheapest fake: helper tested, route missing or wrong url_path.

**Remedy:** Add source/grep guard asserting `url_path="upload/list"` on `CCAssistantViewSet` in failing test before action lands (pattern used in Task 8).

---

### Finding 2D-3 — LOW — Task 4 second fixture adequately anti-overfit

**Location:** Task 4 Step 9b; Gameability Audit table L1874

**Status:** Adequate — `cc_transcript_multitool.jsonl` + distinct kind assertion addresses primary-fixture overfit.

---

## Spec Coverage Snapshot

| Spec area | Plan tasks | Residual gap |
|-----------|------------|--------------|
| §4 upload + list | 3, 9, 9b, 12 | Stale SPEC `input_src` prose (2A-6) |
| §5 hybrid split + download | 6, 10, 12 | Raw path prefix (2A-2); Task 10 paste (2A-1) |
| §6 activity panel | 4, 5, 7, 11, 11a, 12 | jsonl picker (2B-1) |
| §7 transcript DB + recover | 1, 2, 10, 11 | cc_session_id filter (2A-3) |
| §8 Dropbox removal | 6+8 | Atomic coupling documented ✔ |
| §9 session id 3e | 12 | Plan addresses live gap ✔ |
| §10 security | 9, 10 | Guard tests thin (2D-1) |
| §12 testing | 1–12 + 13 | Coverage wiring (2A-4) |

---

## Severity Counts

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 3 |
| MEDIUM | 10 |
| LOW | 4 |

---

## Top Findings (execution blockers first)

1. **2A-1 (HIGH)** — Task 10 paste nests `@action` inside `_iter_file`; download endpoint will not register if pasted literally.
2. **2A-2 (HIGH)** — Raw artifact copy creates `output/raw/raw/…` instead of `output/raw/…`.
3. **2B-1 (HIGH)** — `_newest_jsonl_under` lacks paste-ready, tested algorithm; wrong transcript binding risk on resume.
4. **2A-3 (MEDIUM)** — `recover_transcript` omits `cc_session_id` disambiguation required by interface and Task 13 gate.
5. **2A-4 (MEDIUM)** — Global ≥95% coverage not enforced in per-task verify commands.
6. **2B-3 (MEDIUM)** — `test_cc_realstack.py` update (Step 5c) omitted from Step 8 commit list despite live `artifacts_published` assertion.

---

## Verdict

Iter-7 hardening landed the major functional gaps (zip-if-multiple, turn-scoped keys, bundleId branch, harness pytest-cov, realstack mention, jsonl retry prose). Remaining defects are fixable without architectural rework but include **three HIGH** items—most critically the broken Task 10 paste and raw-path mapping—that can cause silent wrong behavior or missing endpoints if execution proceeds as written.

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE** — patch 2A-1, 2A-2, 2B-1 and the MEDIUM execution-trap items above before unconditional acceptance (UA). UA is **not** granted while HIGH or MEDIUM findings remain open.
