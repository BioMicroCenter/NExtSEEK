# PLAN-3 Phase-2 Pre-Execution Review (Cold Context)

**Reviewer:** Independent adversarial pre-execution review  
**Date:** 2026-06-30  
**Contract reviewed:** `PLAN-3-ui-based-io.md` (authoritative)  
**Spec locked:** `SPEC-3-ui-based-io.md` (E1–E10, §6.2)  
**Code verified:** `translate.py`, `services/cc_assistant.py`, `cc_engine.py`, `cc_provision.py`, `batch_upload/celery_app.py`, `batch_upload/tasks.py`, `services/assistant.py`, `pyproject.toml`

---

## Executive Summary

The plan is structurally sound (TDD seams, spec traceability, Step-2 reuse) but has **four load-bearing contract holes** that will cause Task 13 live-gate failure if executed verbatim: (1) no CC `chat_log` turn persistence, so reload hydration cannot work; (2) Celery upload task never registered in the worker; (3) Task 11 persist snippet placed in the wrong module with out-of-scope variables; (4) `cc_traces` never wired onto live `query_complete` despite SPEC §6.5. Fix these before Task 9/11/12 execution.

**Severity counts:** Critical **5** · Important **6** · Cosmetic **4**

---

## 2A — Vet (Contract completeness vs SPEC + codebase)

### C-1 · CC turn history never persisted — reload success criterion unreachable

**SEVERITY:** Critical  
**Location (PLAN):** Task 11 Step 2; Task 7 projection; SPEC §6.5 “Live + reload”

> “Confirm where the per-turn `entry` is appended to `results_history`/`chat_log` … attach this turn's `trace.model_dump()` there, so reload hydrates it.”

**Why defect:** Verified `services/cc_assistant.py`: the CC branch calls `cc_engine.run_cc_turn(...)` and only invokes `adapter.save()` in `finally` when `ran_ns` is True (NS route). CC turns never write `extra_state["chat_log"]` or `results_history`. `get_session` (`services/assistant.py:501–543`) builds `Turn` objects exclusively from `chat_log` entries (or legacy `results_history` bundles). Adding `Turn.cc_traces` + `entry.get("cc_traces")` projection (Task 7) is inert if no CC turn entries exist. Appending to `extra_state["cc_traces"]` alone does not reach the frontend — `hydrateFromTurns` reads `Turn.cc_traces`, not the raw extra_state list.

**Concrete fix:** Add an explicit Task (or expand Task 11) to persist each CC turn as a `chat_log` entry on terminal `query_complete`: `{user_query, assistant_reply, mode: "cc", ts, artifacts, cc_traces: [trace]}-`, using the canonical extra_state RMW pattern. Call this from `cc_engine.run_cc_turn` (or a post-turn hook with all variables in scope) before/after `send_event`. Optionally also zip-merge `extra_state["cc_traces"]` into projection by index, but **chat_log is the minimum** for messages + artifacts + traces on reload.

---

### C-2 · Task 11 persist snippet in wrong module; referenced variables not in scope

**SEVERITY:** Critical  
**Location (PLAN):** Task 11 Step 2 (`services/cc_assistant.py` snippet)

> “`raw = Path(mount_path).read_bytes()` … `_now_iso()`, `mount_path`, `cc_session_id`, `run_id`, `files_created/modified`, and `result_meta` must be in scope — wire them from the surrounding `_run` closure”

**Why defect:** Verified `_run` closure (`cc_assistant.py:179–361`): it delegates entirely to `cc_engine.run_cc_turn` and contains **none** of those symbols. `mount_path` appears only in the 1c memory-sync block (lines 297–300) for *other* sessions' transcripts, not the current turn. `files_created`/`files_modified`/`result_meta` are produced inside `cc_engine` during publish/translate but never returned. Plan Self-Review flags this as `[CONFIRM@PLAN]` but leaves implementation location ambiguous — a cold implementer will paste into `cc_assistant.py` and get `NameError` or read the wrong jsonl.

**Concrete fix:** Move persist logic into `cc_engine.run_cc_turn` immediately after `_publish_artifacts` and before `send_event(event, data)` where `before`, `after`, `dirs`, `translator.session_id`, `run_id`, and `cc_state_mnt` are in scope. Add helper `_latest_transcript_path(cc_state_dir) -> Path | None` (mirror `_session_metas` jsonl discovery). Derive `files_created = changed - before.keys()`, `files_modified = changed & before.keys()`. Pass `chat_session` + `send_event` only if DB writes must stay in service layer — otherwise inject a `on_turn_complete` callback from `cc_assistant.py` with a typed payload struct.

---

### C-3 · Live `cc_traces` emission missing from `query_complete`

**SEVERITY:** Critical  
**Location (PLAN):** Task 12 Step 2; SPEC §6.5

> “attach `ccTraces` from `d` (the frame now carries trace metadata via the artifacts/trace channel)”

**Why defect:** Task 6 adds `artifacts` to `query_complete`; Task 11 persists trace to DB/extra_state but **no task adds `cc_traces` (or equivalent) to the terminal event payload**. SPEC §6.5 requires the panel to show immediately (“like `debugEntries`”) **and** survive reload. Task 12 assumes the frame already carries trace data — it does not. Hermetic tests will not catch this; only live UI or an integration assertion on `QueryTask.result` would.

**Concrete fix:** In the same persist block (C-2 location), before `send_event`: `data = dict(data); data["cc_traces"] = [trace.model_dump()]` (and keep `artifacts`). Task 12 Step 2 should map `d.cc_traces` explicitly; add a note that this field is new on `query_complete`.

---

### I-1 · Upload list promised in SPEC but absent from PLAN

**SEVERITY:** Important  
**Location (PLAN):** — (missing); SPEC §4 lifecycle

> “A later substep MAY add delete/list; **Step 3 ships upload + list**.”

**Why defect:** Plan covers upload + status poll (Task 9) but no list endpoint/UI for files already in `input/`. Not a hermetic-test gap — it is a spec success-criterion miss.

**Concrete fix:** Add Task 9b (or extend Task 9): `GET …/upload/list/` returning basenames under `input_mnt`, owner-scoped; optional `UploadControl` already-uploaded list in Task 12.

---

### I-2 · CC artifact reload gap (same root as C-1)

**SEVERITY:** Important  
**Location (PLAN):** Task 7, Task 12 Step 5; `services/assistant.py:520`

**Why defect:** Turn projection sets `artifacts = extract_table_artifacts(bundle) if bundle else None`. CC turns have `bundle_id=None`; live artifacts ride `query_complete.artifacts` (Task 6) but reload path ignores event-stored artifacts unless copied onto the `chat_log` entry.

**Concrete fix:** Include `artifacts` from the publish result on the CC `chat_log` entry (C-1 fix). Optionally extend projection: `artifacts = entry.get("artifacts") or extract_table_artifacts(bundle)`.

---

### I-3 · `_Other` schema drift vs locked SPEC §6.3

**SEVERITY:** Important  
**Location (PLAN):** Task 4 `_Other`; SPEC §6.3

**Why defect:** SPEC locks `class _Other(BaseModel): type: str`. Plan uses `type: str | None = None` for blank/`{"_type":"unparsed"}` lines. `parse_transcript` emits `{"_type":"unparsed"}` without a `type` key — plan version is correct for resilience; SPEC literal would fail validation on real transcripts.

**Concrete fix:** Amend SPEC §6.3 to match plan (`type: str | None = None`) or document plan as intentional deviation. Implementer should follow plan, not stale SPEC literal.

---

### I-4 · Wrong translator class name in Task 5 test scaffold

**SEVERITY:** Important  
**Location (PLAN):** Task 5 Step 1

> `from nextseek_api.cc_assistant.translate import StreamTranslator  # adjust if class name differs`

**Why defect:** Verified `translate.py:26`: class is **`CCStreamTranslator`**, not `StreamTranslator`. Plan notes adjustment but embeds the wrong name — copy-paste fails on Step 1.

**Concrete fix:** Replace all `StreamTranslator` references with `CCStreamTranslator` in Task 5 tests and prose.

---

## 2B — Stress Test (Failure modes, edge cases, intermediate states)

### C-4 · Celery upload task will not register in the worker process

**SEVERITY:** Critical  
**Location (PLAN):** Task 9 Step 3; `batch_upload/celery_app.py`

**Why defect:** Verified `celery_app.py:51`: `app.autodiscover_tasks(["nextseek_api.batch_upload"])` only. Step-1c sweep is registered via **explicit import** (`import nextseek_api.cc_assistant.cc_sweep  # noqa`, line 54). `cc_upload_tasks.py` is never imported. Task routes already include `"cc_assistant.*"` (line 37) but routes ≠ registration. Web process may import the task when the upload view loads; **the Celery worker will not**, so `run_cc_upload_task.delay(...)` → `NotRegistered` at runtime.

**Concrete fix:** Add to Task 9: `import nextseek_api.cc_assistant.cc_upload_tasks  # noqa: F401, E402` in `celery_app.py` (mirror cc_sweep). Confirm with `celery -A nextseek_api.batch_upload.celery_app inspect registered | grep cc_assistant.upload` in Task 13.

---

### I-5 · Jsonl path for persist is underspecified

**SEVERITY:** Important  
**Location (PLAN):** Task 11; SPEC §7 write path

**Why defect:** Post-turn jsonl lives under `cc_state/<chat_session_id>/projects/**/*.jsonl` (see `cc_session.store_has_transcripts`). There may be multiple jsonl files; the current turn appends to the active session file. Plan's bare `mount_path` is undefined. Wrong file → wrong `steps`, wrong transcript blob.

**Concrete fix:** Specify: resolve `Path(dirs.cc_state_mnt) / "projects"`, take newest `*.jsonl` by mtime after container exit (same algorithm as `_session_metas` lines 92–93), or pass absolute path from engine if Claude exposes it in stdout.

---

### I-6 · `files_created` vs `files_modified` derivation not specified

**SEVERITY:** Important  
**Location (PLAN):** Task 11 Step 1

**Why defect:** `diff_files` returns a flat `changed` set (new + modified). Plan says to split but gives no code. Wrong split → incorrect `action` on write/edit steps (SPEC §6.2 populated `action`).

**Concrete fix:** Add explicit logic in Task 6/11:
```python
changed = diff_files(before, after)
created = {r for r in changed if r not in before}
modified = changed - created
```
Pass basename lists into `extract_trace`.

---

### I-7 · Task 6 → Task 8 intermediate breakage acknowledged but brittle

**SEVERITY:** Important  
**Location (PLAN):** Task 6 Step 6; Self-Review “Known coupling note”

**Why defect:** `_publish_artifacts` return type changes from `list[str]` to `dict` while Dropbox block still iterates `published` as paths until Task 8. Plan allows commenting out — implementer may land red suite between tasks.

**Concrete fix:** Either combine Tasks 6+8 query_complete handler edits in one commit, or Task 6 Step 6 must fully remove/ guard the Dropbox block in the same step (not defer to Task 8).

---

### I-8 · Transcript recover query omits `cc_session_id`

**SEVERITY:** Important  
**Location (PLAN):** Task 10 Step 2

**Why defect:** Model `unique_together = (chat_session, cc_session_id, turn_id)` but endpoint filters only `(chat_session, turn_id)`. `turn_id` (= `QueryTask.task_id`) is unique per turn today, so collision risk is low; if resume rotates `cc_session_id` mid-session and turn_id scheme changes, wrong row could be returned.

**Concrete fix:** Accept optional `?cc_session_id=` or store lookup using triple from turn metadata; at minimum document invariant that `turn_id` is globally unique per chat turn.

---

## 2C — External Dependencies (Celery, pydantic, zstd, deploy, infra)

### I-9 · `upload_status` should import Celery app from canonical module

**SEVERITY:** Important  
**Location (PLAN):** Task 9 Step 5

> `from nextseek_api.batch_upload.tasks import app as celery_app  # confirm import`

**Why defect:** Verified `batch_upload/views.py:23`: `from .celery_app import app as celery_app`. Importing `app` via `tasks` works only as a re-export side effect and diverges from the established pattern.

**Concrete fix:** Use `from nextseek_api.batch_upload.celery_app import app as celery_app` in both upload_status and cc_upload_tasks (drop try/except shim once confirmed).

---

### I-10 · `zstandard` not in pyproject today — deploy step correct but easy to miss

**SEVERITY:** Important  
**Location (PLAN):** Task 13 Step 1; SPEC E7

**Why defect:** Verified `pyproject.toml`: no `zstandard` entry. Task 13 adds it, but Task 11 import of `cc_transcript_store` in production before rebuild → `ImportError`. Hermetic tests use `--with zstandard` and won't catch prod gap until deploy.

**Concrete fix:** Add `zstandard` to pyproject in Task 1 or Task 2 (same commit as module), not only Task 13. Task 13 remains rebuild/sign-off gate.

---

### I-11 · pydantic unpinned — plan mitigation adequate

**SEVERITY:** (pass)  
**Location (PLAN):** Global Constraints; Task 4 ordered Union

**Verification:** `pyproject.toml:80` lists `pydantic` without pin. Plan mandates ordered `Union` with `_Other` last — matches `parse_transcript` tolerance. No action required beyond Task 4 implementation.

---

### I-12 · Hermetic harness cannot prove endpoint/DB paths — acceptable if Task 13 enforced

**SEVERITY:** Important (process)  
**Location (PLAN):** Global Constraints; Tasks 10–11–13

**Why defect:** By design, owner-scoping, migration apply, Celery E2E, and Playwright gates are deferred to Task 13. Risk: implementer treats green hermetic suite as “done.”

**Concrete fix:** Add explicit checklist item to Tasks 9–11: “Do not mark complete until Task 13 sub-check passes.” Consider one non-DB unit test that source-grep’s `ChatSession.objects.filter(user=request.user` on new endpoints.

---

## 2D — Gameproof (Incentive to cut corners)

### C-5 · Task 11 broad `except Exception: continue` games the live gate

**SEVERITY:** Critical  
**Location (PLAN):** Task 11 Step 2

> `except Exception: logger.exception("cc-step3: trace/transcript persist failed; continuing")`

**Why defect:** Persist failure leaves turn working but panel empty on reload; Task 13 Step 6 (“panel survives reload”) fails while hermetic suite stays green. Implementer can “complete” Tasks 1–12 without fixing root cause.

**Concrete fix:** Task 13 must assert non-empty `cc_traces` on reloaded turn. During development, re-raise in dev/test settings or fail the turn when persist fails. Narrow exception types for expected IO errors only.

---

### G-1 · `extra_state["cc_traces"]` append shallow-copy trap

**SEVERITY:** Important  
**Location (PLAN):** Task 11; Global Constraints extra_state pattern

**Why defect:** `es = dict(sess.extra_state or {}); es.setdefault("cc_traces", []).append(...)` shallow-copies the dict but **not** the nested list — mutates shared list object, violating “never mutate in place” spirit.

**Concrete fix:** `traces = list(es.get("cc_traces") or []); traces.append(...); es["cc_traces"] = traces`.

---

### G-2 · Hermetic grep-guard allows laptop path elsewhere

**SEVERITY:** Cosmetic  
**Location (PLAN):** Task 8 `test_cc_dropbox_grep_guard.py`

**Why defect:** Guard only scans `cc_config.py` and `cc_engine.py`, not `dmac_assistant/config.py` (correctly out of scope per plan) — good. But won't catch reintroduction in `services/cc_assistant.py`.

**Concrete fix:** Extend grep-guard to whole `cc_assistant/` tree (already partial) + assert no “Dropbox” in `services/cc_assistant.py`.

---

### G-3 · Upload validator-only tests allow broken Celery body to ship

**SEVERITY:** Important (process)  
**Location (PLAN):** Task 9

**Why defect:** Task 9 explicitly skips Celery body tests; combined with C-4, upload feature can appear “done” after validator tests pass.

**Concrete fix:** Tie Task 9 completion to worker registration check (C-4) + Task 13 Step 4 live upload.

---

## Cosmetic / Non-blocking

1. **SPEC §4 says `input_src`; plan correctly uses `input_mnt`** for Django-in-container writes — documentation drift only; follow plan Task 3/9.
2. **`int_time_unique()`** referenced in Task 9 but not defined — implementer must add one-liner; mirror batch_upload timestamp idiom.
3. **Task 5 note says `StreamTranslator`** while codebase has `CCStreamTranslator` — covered in I-4.
4. **Artifact download** (`Task 10`) lacks `resolve()` containment check under `art_dir`; mirror `services/assistant.py` artifact path guards if symlinks are a concern on dev instance.

---

## Verified Claims (plan ↔ code)

| Claim | Verdict |
|-------|---------|
| `_handle_result` owner class | **`CCStreamTranslator`** (plan says `StreamTranslator`) |
| Celery `@app.task` pattern | Confirmed in `batch_upload/tasks.py:17` via `celery_app.app` |
| `_publish_artifacts` at `:639` | Confirmed; returns `list[str]` today |
| Dropbox reply at `:580–587` | Confirmed |
| `UserDirs` has `output_mnt`, no `input_mnt` | Confirmed — Task 3 additive field valid |
| pydantic unpinned | Confirmed `pyproject.toml:80` |
| Migration dep `0006_merge_extra_state_guards` | Confirmed exists |
| Celery route `cc_assistant.*` | Confirmed in `celery_app.py:37` (registration still needs import — C-4) |

---

## Top Findings (priority order)

1. **C-1** — No CC `chat_log` persistence → reload cannot hydrate turns, traces, or artifacts.  
2. **C-4** — `cc_upload_tasks` not imported in `celery_app.py` → upload jobs never run in worker.  
3. **C-2** — Task 11 persist in `cc_assistant._run` with undefined `mount_path` / diff metadata.  
4. **C-3** — `cc_traces` not emitted on live `query_complete` (SPEC §6.5 immediate panel).  
5. **C-5** — Swallowed persist exceptions allow false “done” through Task 12 with Task 13 failure.

---

## Recommended Pre-Flight Amendments (before execution)

1. Insert **Task 11a: CC chat_log turn writer** (user_query, reply, artifacts, cc_traces, ts, mode).  
2. Add **celery_app import** for `cc_upload_tasks` to Task 9.  
3. Relocate **trace/transcript persist + live emit** into `cc_engine.run_cc_turn` with explicit jsonl resolution.  
4. Fix **CCStreamTranslator** naming in Task 5.  
5. Add **upload list** or mark SPEC §4 “upload + list” as deferred with user sign-off.  
6. Add **`zstandard` to pyproject** early (Task 1/2), not only Task 13.

---

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
