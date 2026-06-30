# Fresh Phase 2 Adversarial Review — PLAN-3-ui-based-io.md (iteration 2-fresh, independent cold context)

**Target:** `/home/taishajo/work/NExtSEEK/nextseek_api/cc_assistant/PLAN-3-ui-based-io.md`  
**Locked design:** `SPEC-3-ui-based-io.md` (E1–E10, enriched §6.2)  
**Project guide:** `AGENTS.md`  
**Live repo spot-check:** `feat/dmac-assistant-full-integration` baseline (pre-implementation; no Step-3 code landed)  
**Reviewer:** Independent fresh adversarial reviewer (no prior review/fix-log files read)

---

## Phase 2 Section Presence (hard gate)

| Required section | Present | Location |
|------------------|---------|----------|
| `## Permissions Required` | Yes | L1606–1623 |
| `## Risk Register` | Yes | L1626–1640 |
| `## Dependency Validation` | Yes | L1643–1654 |
| `## Gameability Audit` | Yes | L1657–1669 |
| `## Phase 2 Vetting Log` | Yes | L1673–1680 |

All five Phase 2 appendices exist. The plan's internal vetting log (iteration 2 → UNCONDITIONAL_ACCEPTANCE) is **not** treated as evidence; this review re-evaluates independently.

---

## Prior Critical Findings — Verification

| Prior finding | Addressed in plan? | Evidence |
|---------------|-------------------|----------|
| **Task 11a `chat_log` writer** | **Yes** | Dedicated Task 11a (L1416–1431): `_append_cc_chat_log`, projection fix for CC `artifacts`, explicit reload-hydration rationale vs `get_session`/`chat_log` path (`assistant.py:501–529` today). |
| **Persist in `cc_engine.run_cc_turn`, not `cc_assistant._run`** | **Yes** | Task 11 header (L1387–1404) documents why `_run` lacks symbols; persist block specified inside `run_cc_turn` after `_publish_artifacts`. Live code confirms `_run` only calls `run_cc_turn` (`cc_assistant.py:337–349`) and post-turn publish lives in `cc_engine.py:572–588`. |
| **Celery worker registration for `cc_upload_tasks`** | **Partially** | Task 9 Step 3b (L1185–1191) adds explicit `import nextseek_api.cc_assistant.cc_upload_tasks` to `batch_upload/celery_app.py` with Task 13 inspect grep. **Gap:** Step 7 commit command (L1268–1270) omits `celery_app.py` — see HIGH finding below. |
| **`CCStreamTranslator` naming** | **Yes** | Task 5 (L656–689) uses `CCStreamTranslator`; confirmed live at `translate.py:26`; `_handle_result` at `:130–156`. |
| **Task 9b upload list** | **Yes (thin)** | Task 9b (L1275–1289) adds `upload/list` per SPEC §4 lifecycle. Steps are high-level only — see MEDIUM finding. |
| **Atomic Task 6+8 `cc_engine` handler** | **Yes** | Task 6 Step 6 (L894–910) + Phase 2 coupling rule (L910, L1602); Task 8 defers Dropbox removal to Task 6 Step 6 (L1006–1008). |
| **`zstandard` in Task 1 (not Task 13-only)** | **Yes** | Global Constraints L30–31; Task 1 Step 5 (L161–163); hermetic command includes `--with zstandard` (L19). Task 13 Step 1 is a deploy re-check (acceptable). |

---

## 2A — Vet (execution readiness + permissions)

### Finding 2A-1 — HIGH — Task 9 commit omits worker registration file

**Location:** Task 9 Step 3b (L1185–1191) vs Task 9 Step 7 commit (L1268–1270)

**Quote:** Step 3b requires editing `nextseek_api/batch_upload/celery_app.py`; Step 7 `git add` lists only `cc_upload_tasks.py`, `cc_assistant.py`, and the validator test.

**Why defect:** Without the `celery_app.py` import, `run_cc_upload_task` remains **NotRegistered** despite route `cc_assistant.*` in `task_routes` (live `celery_app.py:35–37`). Upload returns 202 but jobs never execute — matches Risk Register rank #2 catastrophic mode.

**Fix:** Extend Task 9 Step 7 to `git add nextseek_api/batch_upload/celery_app.py` and mention it in the commit message; add a grep/inspect assertion in Step 6 or Task 13 Step 4.

### Finding 2A-2 — MEDIUM — Task 11 Step 5 conflates `chat_log` RMW with `es["cc_traces"]` RMW

**Location:** Task 11 Step 5 persist bullet (L1401–1402)

**Quote:** `Append chat_log entry: {...} using canonical extra_state RMW (traces = list(es.get("cc_traces") or []); traces.append(...); es["cc_traces"] = traces`

**Why defect:** The parenthetical RMW pattern is for `extra_state["cc_traces"]` (SPEC E5), not `chat_log`. Reload hydration reads **`chat_log` entries** (`assistant.py:501–529`), not top-level `cc_traces`. Task 11a mitigates via `_append_cc_chat_log`, but Task 11's combined bullet can cause an implementer to skip `chat_log` or use the wrong key — Risk Register #1.

**Fix:** Split into two explicit RMW snippets: (1) `chat_log` append with `log = list(es.get("chat_log") or [])`; (2) optional/required `es["cc_traces"]` append per locked E5. Cross-reference Task 11a as blocking dependency.

### Finding 2A-3 — MEDIUM — SPEC E5 storage path vs reload seam not reconciled in one place

**Location:** SPEC §6.5 / E5 vs Task 11 + Task 11a

**Why defect:** Locked E5 names `extra_state["cc_traces"]`; live Turn projection is driven by `chat_log`. Plan correctly adds Task 11a but never states whether **both** stores are required or `chat_log`-embedded `cc_traces` satisfies E5. Ambiguity can yield SPEC-compliant but reload-broken implementations.

**Fix:** One sentence in Task 11/11a: "Reload source of truth is `chat_log[].cc_traces`; also mirror append to `extra_state['cc_traces']` for E5" (or escalate SPEC amend if chat_log-only is intended).

**Permissions catalogue:** Phase 2 `## Permissions Required` table (L1606–1621) is complete and matches task needs (hermetic pytest, Celery broker, MEDIA_ROOT, DMAC mounts, ORM migrate, Docker deploy, Playwright). No missing permission classes identified.

---

## 2B — Stress Test

Risk Register (L1626–1639) covers the highest coupling points (11/11a persist, Celery registration, 6+8 atomic handler, owner-scoping, frontend bundle staleness). **Additional stress gaps:**

### Finding 2B-1 — MEDIUM — Task 9b failure mode under-documented

**Location:** Task 9b (L1275–1289)

**Likely failure:** Agent implements upload (Task 9) but skips or stubs list endpoint; SPEC §4 "ships upload + list" unmet with no live-gate check naming list explicitly.

**Fix:** Add Task 13 Step 4 sub-bullet: confirm `GET …/upload/list` returns basenames after upload; add hermetic test snippet for `list_input_files` matching Task 9 validator depth.

### Finding 2B-2 — LOW — Task 11 jsonl resolution algorithm differs from SPEC §7 cite

**Location:** Task 11 Step 1 (L1397) vs SPEC §7 "read_bytes() at cc_assistant.py:103/300"

**Note:** Plan's newest-`*.jsonl` under `cc_state_mnt/projects` is reasonable and better placed in `run_cc_turn`, but implementer following SPEC literally might look at wrong file. Non-blocking if Task 11 is followed.

Rollback/pause conditions in Risk Register are adequate for Step 3 scope.

---

## 2C — Validate External Dependencies

| Dependency | Plan claim | Verification |
|------------|-----------|--------------|
| `zstandard` | Task 1 add; bomb guard via `stream_reader` | PyPI package supports API in plan snippet; **not yet in root `pyproject.toml`** (expected pre-Task-1). OK. |
| pydantic v2 unpinned | Ordered `Union`, `_Other` last | Matches repo pattern; plan mitigates. OK. |
| Celery `batch_upload.celery_app` | Import path + explicit register | Live `views.py:23` uses `from .celery_app import app`; plan's absolute import in `cc_upload_tasks.py` is valid. Registration step present but commit gap (2A-1). |
| Django migration 0007 | Depends on `0006_merge_extra_state_guards` | Consistent with plan Task 2. OK. |
| Vitest / `build:embedded` | Task 12/13 | Standard frontend toolchain; OK. |

No blocking external dependency risks beyond Celery registration execution trap.

---

## 2D — Gameproof

### Finding 2D-1 — MEDIUM — Gameability remedy for Task 4 not wired into task steps

**Location:** Gameability Audit row for Task 4 (L1667) vs Task 4 steps (L331–636)

**Success condition (Task 4):** "PASS (5 tests)" on single fixture `cc_transcript_sample.jsonl`.

**Cheapest fake:** Overfit `extract_trace` to the one fixture; `classify_tool_use` drift vs 1c undetected if suite gaps exist (no dedicated `_tool_use_line` byte-identical oracle test in plan — relies on "full suite" gate).

**Gameability Audit remedy:** "Add second fixture with different tool names; 1c full suite gate."

**Defect:** Task 4 contains **no step** adding a second fixture or extra test — audit claims a remedy not cashed in task steps (hardener discipline violation).

**Fix:** Add Task 4 Step 6.5: second fixture + test asserting different tool names/kinds; or downgrade Gameability row to "accepted: 1c full suite only" and cite existing hermetic tests under `test_cc_summary*.py` if any cover `_tool_use_line` output.

### Finding 2D-2 — MEDIUM — Task 9b gameable as stub endpoint

**Location:** Task 9b (L1286–1289)

**Cheapest fake:** Empty `@action` returning `{"files": []}` always — passes no automated test (none specified beyond optional helper test).

**Fix:** Require hermetic test + Task 13 live assertion that uploaded basename appears in list response.

### Positive gameproof hardening (no finding)

- Task 13 requires `evidence/3-ui-based-io-live/live_gate_transcript.txt` (L1571–1573) — closes prose-only "done."
- Task 11 failure policy: re-raise in dev; reload gate (L1404, Gameability L1663).
- `--cov-fail-under=95` on pure modules (L29) — closes coverage gaming for Tasks 1, 3–7, 9 validator.

---

## Non-blocking cosmetic notes

- Task 9 references `int_time_unique()` without a snippet (L1259); trivial for implementers.
- Task 13 Step 1 duplicates Task 1 `zstandard` work — redundant but harmless deploy reminder.
- Plan `[CONFIRM@PLAN]` placeholders resolved in Self-Review (L1600); live `translate.py` still lacks `num_turns`/`duration_ms` until Task 5 (expected).
- SPEC §4 cites `input_src`; plan correctly uses `input_mnt` for Django mount writes (Task 3) — document drift only.

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 5 |
| LOW | 1 |

**Top fixes before execution:** (1) Include `celery_app.py` in Task 9 commit + verify registered task; (2) Clarify Task 11/11a dual persist (`chat_log` vs `es["cc_traces"]`); (3) Wire Task 4 second-fixture remedy into task steps or audit; (4) Harden Task 9b with tests + live list check.

Prior iteration-critical defects (11a, `run_cc_turn` persist, translator name, 6+8 atomicity, Task 1 zstd) are **substantively addressed** in plan text. Remaining gaps are execution-trap and gameproof wiring issues, not architectural rejection.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
