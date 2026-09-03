# PLAN-3 Phase-2 hardener fix-log — iter-18 (responds to .vetting/plan-3-phase2-review-18-fresh.md)

Role: HARDENER (not reviewer). Target edited: `nextseek_api/cc_assistant/archive/PLAN-3-ui-based-io.md` only.
SPEC-3 (locked design) NOT edited. Phase-2 Vetting Log / status line NOT touched (orchestrator-owned).

All technique/coverage claims below were verified empirically in the actual harness
(Python 3.12.13, `uv run --no-project --with pytest --with pytest-cov`, zstandard, pydantic 2.13.4),
running probes OUTSIDE the repo tree (scratchpad) so no build artifact was written into the repo.

---

## Verification table (defect → before → after → evidence)

| # | Sev | Finding | Before (defect) | After (fix) | Empirical evidence |
|---|-----|---------|-----------------|-------------|--------------------|
| 1 | HIGH | Task 9 validator test ERRORs at import (celery import block; no `--with celery`) | `validate_upload_filename` lived in `cc_upload_tasks.py`, whose top-level `try/except` imports `celery` in the `except` leg → uncaught `ModuleNotFoundError` at collection | Split the validator into a **celery-free** new module `cc_upload_validate.py`; `cc_upload_tasks.py` and the test and the upload action all import from it. Updated Task 9 Files, Interfaces, Step 1 test import, Step 2 expected-fail message, Step 3 paste-ready (two code blocks), Step 5 action import, Step 7 `git add`; added the module to the File-Structure Create list. | Probe B: `uv run --no-project --with pytest python -c "import celery"` → `ModuleNotFoundError`; `from nextseek_api.cc_assistant.cc_upload_validate import validate_upload_filename` → **import OK** with celery absent. Validator test (plan's exact cases) → **9 passed**. |
| 5 | MEDIUM | Task 9 ≥95% floor unreachable (validator shares module with celery task body) | `--cov=cc_upload_tasks` would measure the untested celery body + import guard | Floor now targets the pure `cc_upload_validate` only. Found the validator still hit **91%** (1 dead defensive line `if base != name or not base:` — provably unreachable once `/`,`\`,NUL,absolute are rejected). Added `# pragma: no cover` (justified-exception) + an explanatory note. | Without pragma: `cc_upload_validate.py 11 1 91%` → `--cov-fail-under=95` **FAILS (90.91%)`. With pragma: `9 0 100%` → **floor reached (100%)`. |
| 2 | HIGH | Task 11 Step 1 `monkeypatch.setattr(<Path>,"stat",…)` DOA on Py3.12 | Per-instance `stat` stub on `PosixPath` (`__slots__`) | Rewrote `test_cc_newest_jsonl.py` to create real files and set mtimes with `os.utime`, asserting newest-jsonl selection on real stat data (min_mtime=5.0 → `new`; 20.0 → `None`). | Probe A: new `os.utime` test → **passed**; old technique `p.stat = lambda: 1` → `pytest.raises(AttributeError)` **passes** (confirms DOA on 3.12). `cc_engine` imports hermetically (so the `_newest_jsonl_under` import in the test resolves). |
| 3 | MEDIUM | Task 10 `recover_transcript` uses `settings` with no import → live `NameError` | Function body referenced `getattr(settings, …)` but never imported it; only `_start_task` imports it locally at module `:168` | Added `from django.conf import settings` as the first local import inside `recover_transcript` (matches the real module's local-import pattern + the `upload` action). | Confirmed live source `services/cc_assistant.py` imports settings only locally at `:168`; edit at PLAN line 1626 placed inside `recover_transcript`. |
| 4 | MEDIUM | ≥95% floor declared globally, wired only in Task 6 | Tasks 1, 3, 4, 5, 7, 9, 9b ran plain `pytest` | Appended `--with pytest-cov … --cov=<module> --cov-fail-under=95` to each task's actual verify command, naming the module that task actually adds/edits, and rewrote the Global Constraint to name the gated module per task. Tasks 1, 3, 4, 6, 7, 9, 9b are hard-gated; Task 5 (translate) is the documented exception (see escalation below). | Per-module reachability measured: `cc_transcript_store` **100%**; `cc_provision` **96%** (needs all 4 provision test files — command expanded accordingly); `cc_trace` **98.61%** (line 60 `action="modified"` is the only miss, well above floor); `models_api` **96% on import** (declarative pydantic); `cc_upload_validate` **100%** (with pragma); `cc_upload_list` **100%** (after adding missing-dir test, see below). |
| 6 (cosmetic) | LOW | File-Structure Modify list cites stale `lib/api/chatApi.ts` | line 61 | Changed to `lib/services/chatApi.ts` (the real path; Task 12 already used it). | `find chat_frontend/src -name chatApi.ts` → `chat_frontend/src/lib/services/chatApi.ts`. |

### Collateral reachability fixes required by finding 4 (rule 6 — make the wired gate reachable, never lower it)
- **cc_upload_list (Task 9b):** the natural impl guards the not-yet-created `input/` dir (`FileNotFoundError → []`); the plan's single happy-path test left that line uncovered → **90%**, so the wired floor would block a faithful implementer. Added a second test `test_list_input_files_missing_dir_returns_empty` + a one-line impl note. Re-measured: **100%**. (Surgical; serves only gate reachability.)

---

## Escalation — Task 5 (translate) whole-module ≥95% gate is genuinely unreachable

Finding 4 lists Task 5 among the tasks needing a `--cov … --cov-fail-under=95` gate, and its qualifier says "the `--cov` module must name the module that task actually **adds**." Task 5 **adds no module** — it appends two keys (`num_turns`, `duration_ms`) to the `query_complete` dict inside `translate._handle_result`. Empirical findings:

- `translate.py` is 85 measurable statements; the existing `test_translate.py` already covers **94%**; the 5 uncovered lines are **`58, 68, 97, 104, 105/123`** — all in `handle` / `finalize` / `_handle_system` / `_handle_assistant` / `_handle_user` edge branches, **none inside `_handle_result`** (verified by `awk` on the real source + `--cov-report=term-missing`).
- Task 5's seam test alone (`_handle_result` only) covers **27%** of `translate.py`.
- The existing test imports `from translate import …` (sys.path hack) while the new seam test imports the dotted `nextseek_api.cc_assistant.translate`; running both under one `--cov` collapses coverage to **"No data was collected"** (the two import names defeat file attribution).

Therefore a whole-module ≥95% gate on `translate.py` cannot be met within Task 5's surgical scope without writing tests for unrelated branches (scope creep, rule 1/5) or extracting a module (collateral edit to the locked seam, rule 1). Per discipline rule 6 I did **not** lower or delete any real gate: every actual pure module still carries the unchanged **95%** floor. I resolved the contradiction by making the Global Constraint **precise** about which module each task gates, and marking Task 5 as not-whole-module-gated with the empirical reason; Task 5 now runs `--cov=…translate --cov-report=term-missing` (informational, no fail-under). The `_handle_result` change remains proven by the two new assertions (Step 1) and the Task 13 live gate. **This is recorded as an escalation, not a silent weakening.** It does not touch SPEC-3.

---

## Self-verification probes (run after edits, before this log)

1. **Celery-free relocation** — `import celery` → ModuleNotFoundError; `cc_upload_validate` imports cleanly; plan's validator test → 9 passed; `--cov=cc_upload_validate --cov-fail-under=95` → 100% PASS.
2. **Py3.12 os.utime rewrite** — new test passes on real stat data; old `monkeypatch`/per-instance `stat` raises `AttributeError` (DOA confirmed).
3. **Coverage cross-check (each `--cov=<module>` names the module that task creates/edits):**
   - Task 1 → `cc_transcript_store` (Files: Create `cc_transcript_store.py`) → 100%.
   - Task 3 → `cc_provision` (Files: Modify `cc_provision.py`) → 96% (all 4 provision tests).
   - Task 4 → `cc_trace` (Files: Create `cc_trace.py`) → 98.61%.
   - Task 6 → `cc_artifacts` (unchanged; pre-existing gate).
   - Task 7 → `nextseek_api.assistant.models_api` (Files: Modify `models_api.py`) → 96% on import.
   - Task 9 → `cc_upload_validate` (Files: Create `cc_upload_validate.py`) → 100%.
   - Task 9b → `cc_upload_list` (Files: Create `cc_upload_list.py`) → 100% (after missing-dir test).
4. **cc_engine hermetic import** — `from nextseek_api.cc_assistant.cc_engine import _safe_relpath` → OK (Task 11 test's `_newest_jsonl_under` import resolves).
5. **SPEC-3 contract check** — grep of SPEC-3 shows **no** contract on `cc_upload_tasks` internal layout, the `validate_upload_filename` location, or coverage; the relocation and coverage edits are plan-level only → no SPEC escalation needed.

---

## Defects unresolved
- **Task 5 whole-module coverage gate** — intentionally not made a hard `--cov-fail-under=95` gate; escalated above with empirical reasoning. No real pure-module gate was lowered. (This is the only item not landed as a literal "append fail-under to Task 5" because doing so would create an unreachable/uncashable gate.)

---

## Follow-up suggestions (NOT applied — out of surgical scope / not in the defect list)
- **Task 6 Step 5b template** itself shows the cov command without `--with pytest-cov` (line ~915); appending `--cov` to a `--with pytest` command would error on an unknown arg. My added commands all include `--with pytest-cov`; Task 6's template should too. Left untouched (not in the iter-18 defect list).
- **Upload size-cap env name drift:** SPEC-3 §6 mentions `CC_UPLOAD_MAX_TOTAL_BYTES`, while Task 9's upload action reads `BATCH_UPLOAD_MAX_TOTAL_BYTES` and Task 10 reads `CC_TRANSCRIPT_MAX_BYTES`. Not flagged by iter-18; left as-is.
- **cc_trace line 60** (`action = "modified"`) is the only uncovered line (98.61%); a `files_modified=[…]` case in Task 4/9b would bring it to 100%, but the gate is already satisfied — left as-is to avoid scope creep.
