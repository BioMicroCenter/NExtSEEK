# Phase-2 fresh re-vet (iter 18) — TARGET: PLAN-3-ui-based-io.md

Cold-context adversarial review of `nextseek_api/cc_assistant/PLAN-3-ui-based-io.md` against SPEC-3 (locked E1–E10, §6.2 enriched trace), the Step-2 primitives, and the live cc_assistant source. All library/test-technique claims below were verified empirically in the actual environment (Python 3.12.13, pydantic 2.13.4, zstandard, and the `uv run --no-project --with pytest` hermetic harness the plan mandates).

Net: the architecture is sound and most paste-ready code is faithful to the real source (verified: `CCPaths` fields, `build_user_dirs`/`UserDirs` shape, `_tool_use_line` byte-identical refactor, `Turn` `extra="forbid"`, the chat_log projection at services/assistant.py:521-530, `register_job`/`user_owns_job` signatures, celery `cc_assistant.*` route, `run_cc_turn` `run_id`/`dirs` scope, cc_state_key always set, zstd stream_reader bomb-bound, pydantic ordered-union routing). But two HIGH defects break paste-ready hermetic tests as literally written, plus three MEDIUM contract gaps.

---

## 2A — Vet (permissions / paths / execution snags)

**[MEDIUM] Coverage gate is declared globally but wired into only ONE task command.**
Location: Global Constraints — "Coverage targets (Phase 2 hardened)" / "every listed task verify command must append `--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95` (Task 6 Step 5b is the template)."
Defect: Only Task 6 Step 5b actually appends `--cov ... --cov-fail-under=95`. The verify commands for the other pure-module tasks — Task 1 Step 4, Task 3 Step 4, Task 4 Step 9, Task 5 Step 4, Task 7 Step 5, Task 9 Step 4, Task 9b Step 3 — run plain `pytest` with no `--cov`. As literally written, the ≥95% floor is unenforced for every pure module except cc_artifacts; a lazy implementer runs the shown commands and never trips it. The Global Constraint text and the per-task commands contradict each other.
Fix: Append `--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95` to each pure-module task's actual verify command (matching the Task 6 template), or relax the Global Constraint to name only the modules it actually gates.

**[LOW] `settings` not module-level in services/cc_assistant.py — see 2B for the live NameError it causes.** (Permissions cataloguing: the recover endpoint needs `django.conf.settings`, which is imported only locally inside `_start_task` today.)

Permissions/paths otherwise confirmed adequate: hermetic `uv run` (Tasks 1,3–7,9,9b), `makemigrations` no-DB (Task 2), Celery broker + `batch_upload` queue (Task 9/13), `MEDIA_ROOT` staging, `DMAC_USER_ROOT` mount roots, docker socket for Task 13. The Permissions Required table is accurate.

---

## 2B — Stress Test

**[HIGH] Most likely failure — Task 9 validator test ERRORs at import under the prescribed harness (missing `--with celery`).**
Location: Task 9 Step 3 (`cc_upload_tasks.py` top-level block) + Step 4 ("Expected: PASS (validator cases)").
The module's top-level import is:
```python
try:
    from nextseek_api.batch_upload.celery_app import app   # needs django+celery
except Exception:
    from celery import shared_task as _shared              # <-- uncaught ModuleNotFoundError
    ...
else:
    app_task = app.task
```
The Task 9 verify command is `uv run --no-project --with pytest python -m pytest ... test_cc_upload_validate.py` — **no `--with celery`**. Empirically confirmed in this env: `uv run --no-project --with pytest python -c "import celery"` → `ModuleNotFoundError: No module named 'celery'`, and importing the exact try/except block raises an *uncaught* `ModuleNotFoundError` from the `except` leg. Therefore `test_cc_upload_validate.py` (which does `from nextseek_api.cc_assistant.cc_upload_tasks import validate_upload_filename`) fails at collection — it cannot reach the validator assertions. Step 4's "Expected: PASS" is false; the only hermetic seam for Task 9 is unrunnable. (Step 2's expected-fail message is fine because the module does not yet exist.)
Fix (preferred): move `validate_upload_filename` into a celery-free module (e.g. reuse the `cc_upload_list.py` pattern or a `cc_upload_validate.py`) and import it from BOTH `cc_upload_tasks.py` and the test, so the validator imports with zero celery dependency. Alternatively add `--with celery` to the Task 9 command AND make the `celery_app` import not trigger `django.setup()` at import — but a dedicated pure module is cleaner and also fixes the coverage gap below.

**[MEDIUM] Coverage floor unreachable for Task 9 as structured (compounds the HIGH above).**
Location: Global Constraints "9 validator" ∈ the ≥95% set.
`cc_upload_tasks.py` mixes the pure `validate_upload_filename` with the celery `run_cc_upload_task` body and the import-guard try/except. `--cov=nextseek_api.cc_assistant.cc_upload_tasks` would measure all of it; the task body + guard are never exercised hermetically, so ≥95% line coverage is unattainable. The same module-split fix resolves both this and the HIGH.

**[HIGH] Task 11 paste-ready helper test cannot run — `monkeypatch.setattr(<Path>, "stat", ...)` is illegal on Python 3.12.**
Location: Task 11 Step 1, `tests/test_cc_newest_jsonl.py`:
```python
old = tmp_path / "old.jsonl"; old.write_text("x"); monkeypatch.setattr(old, "stat", lambda: ...)
new = tmp_path / "new.jsonl"; new.write_text("y"); monkeypatch.setattr(new, "stat", lambda: ...)
```
`pathlib.PosixPath` uses `__slots__`; setting a per-instance `stat` attribute raises. Empirically confirmed on this box (Python 3.12.13): `p.stat = lambda: 1` → `AttributeError: 'PosixPath' object attribute 'stat' is read-only`. So `monkeypatch.setattr(old, "stat", ...)` errors before any assertion; the test is dead-on-arrival. (It is also conceptually wrong: `_newest_jsonl_under` calls `p.stat().st_mtime` inside both the filter and `max(key=...)`, so per-instance stubbing wouldn't model the real call pattern anyway.)
Fix: build real files and set distinct mtimes with `os.utime(path, (t, t))`, then assert `_newest_jsonl_under(tmp_path, min_mtime=5.0) == new` and `... min_mtime=20.0 is None`. No monkeypatching of Path needed.

**Most catastrophic failure (hard to reverse):** Task 11/11a persist-in-wrong-place → users trust a panel that vanishes on reload (Risk Register rank 1). The plan mitigates well: persist relocated into `cc_engine.run_cc_turn` (verified `run_id`/`dirs`/`translator` all in scope there), single-owner RMW in `_append_cc_turn_complete`, re-raise on missing jsonl, and Task 13 Step 6 asserts non-empty `cc_traces` after reload via scripted `jq`. Acceptable.

**Hidden dependency (handled):** persist reads `Path(dirs.cc_state_mnt) / "projects"`; `cc_state_mnt` is `None` when `cc_state_key` is falsy. Verified `cc_state_key = str(chat_session.session_id)` is always set on the CC branch (services/cc_assistant.py:223), so no `None`-path crash. No action needed, but worth a one-line guard.

**Ambiguous success condition (handled):** Task 13's "panel survives reload" is hardened into a committed `live_gate_transcript.txt` with a JSON excerpt showing non-empty `turns[*].cc_traces` + chat_log `assistant_reply`. This is also the artifact Step 7 (PLAN-7) consumes; the producer/consumer contract is satisfied (Task 13 Step 8 lists it, Step 9 commits it as the SPEC-7 §8 hard gate).

**Rollback conditions:** `:pre-step3` snapshot + `rollback.sh`; per-change sign-off on the running instance; E8 default change and dead-config removal ship as isolated diffs. Adequate.

---

## 2C — Validate External Dependencies

- **zstandard** (Task 1): VERIFIED in-env — `ZstdDecompressor().stream_reader(<bytes>)` accepts a bytes blob and the bomb-bound `len(out) > max_bytes` path is reachable on the first 1 MiB chunk for a 2 MiB payload. `compress`/`decompress` round-trip and the `TranscriptTooLarge` test are correct. Add `zstandard>=0.25` in Task 1 — OK.
- **pydantic v2 unpinned** (Task 4): VERIFIED in-env (2.13.4) — the ordered `Union[_Assistant,_User,_Other]` routes `user`→`_User`, `assistant`→`_Assistant`, `{"_type":"unparsed"}`→`_Other`, `{"type":"summary",...}`→`_Other`. This is load-bearing: step `status` pairing requires `user` records to validate as `_User`, and they do. OK.
- **orjson** (Task 4): reused via existing `parse_transcript`. OK.
- **Celery** (`batch_upload.celery_app`): route `cc_assistant.*` → `batch_upload` queue confirmed; explicit worker import `import nextseek_api.cc_assistant.cc_upload_tasks` mirrors the existing `cc_sweep` import. Registration approach OK — but see the HIGH on the import-time celery dependency leaking into the hermetic validator test.
- **Vitest / `build:embedded`**: confirmed `chat_frontend/package.json` has `"test": "vitest run"` and `"build:embedded": "tsc -b --noEmit && vite build --config vite.config.embedded.ts"`. OK.

---

## 2D — Gameproof

**[MEDIUM] Latent live-only NameError in the recover endpoint hides behind the "import-check only" success bar.**
Location: Task 10 Step 2 `recover_transcript`: `decompress(bytes(row.blob), max_bytes=getattr(settings, "CC_TRANSCRIPT_MAX_BYTES", 256 * 1024 * 1024))`.
`services/cc_assistant.py` imports `settings` ONLY locally inside `_start_task` (line 168); there is no module-level `from django.conf import settings`, and Task 10 Step 1's "add at module top" list does not include it, nor does the action body import it. At call time this raises `NameError: name 'settings' is not defined`. Because endpoints are explicitly "import-checked only" hermetically (Task 10 Step 3), every hermetic gate passes green while the endpoint is broken — it only fails in the Task 13 live gate. Cheapest fake: implementer sees the suite green and marks Task 10 done.
Fix: add `from django.conf import settings` inside `recover_transcript` (or at module top). The `upload` action correctly imports settings locally; `recover_transcript` is the lone omission.

**No-op / mutation observations (mostly closed):**
- Task 4 extractor is anti-overfit (second `multitool` fixture + distinct-`kind` assertion). Good. Minor residual: the orphan-`tool_result` branch (`step = by_id.get(...)` → `None` → skip) is not exercised by either fixture, a plausible sub-95% line if the coverage gate is wired per 2A; add one record whose `tool_result.tool_use_id` has no matching `tool_use`.
- Task 8 grep-guard scans both `cc_engine.py` and `services/cc_assistant.py` for "Saved to your Dropbox" and `artifacts_published`, and asserts `/srv/dmac/users` present + `/Users/taishajoseph` absent. Verified the live strings exist today (cc_engine.py:584-587 Dropbox block; cc_config.py:15 laptop default). Guard is real, not gameable by moving to a comment within those two files. OK.
- Task 13 success is gated on committed transcript evidence, not agent prose. OK.

---

## Non-blocking cosmetic notes

- **File Structure "Modify" list (line 61)** cites `lib/api/chatApi.ts`, but the real file is `chat_frontend/src/lib/services/chatApi.ts` (verified) and Task 12 correctly uses `lib/services/chatApi.ts`. The binding task is right; only the inventory line is stale.
- **Task 4 `_Other.type: str | None = None`** diverges from SPEC §6.3 prose (`type: str`). This is documented in the Self-Review and is in fact MORE correct: the spec's required `type` would crash on real `{"_type":"unparsed"}` lines (no `type` key). Keep the plan's relaxation; the spec prose is the stale side. Not a defect.
- **Phase 2 status line** still says "INCOMPLETE — iter-17". Stale once this review lands; no action.

---

### Summary table

| Sev | Finding | Location |
|-----|---------|----------|
| HIGH | Top-level celery import breaks the hermetic validator test (no `--with celery`; except-leg imports celery) | Task 9 Step 3/4 |
| HIGH | `monkeypatch.setattr(Path,"stat",...)` raises on Py3.12 → helper test DOA | Task 11 Step 1 |
| MEDIUM | `recover_transcript` uses `settings` with no import → live NameError, invisible to import-only gate | Task 10 Step 2 |
| MEDIUM | ≥95% coverage gate declared globally but appended only in Task 6 (unenforced elsewhere) | Global Constraints |
| MEDIUM | Task 9 ≥95% unreachable while validator shares a module with the celery task body | Task 9 / Global Constraints |
