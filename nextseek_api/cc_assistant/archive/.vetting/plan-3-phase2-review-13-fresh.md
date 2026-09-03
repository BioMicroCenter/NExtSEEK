# TARGET: `/home/taishajo/work/NExtSEEK/nextseek_api/cc_assistant/PLAN-3-ui-based-io.md`

## 2A — Vet

### Finding 2A-1 — HIGH — Task 11 persist paste omits on-disk transcript copy
- **Location:** Task 11 — *"**Also (SPEC §3/E3):** after reading `raw`, `copy2` to `Path(dirs.output_mnt) / "raw" / f"transcript-{run_id}.jsonl"`"* vs the *"Minimal persist block"* code fence (lines ~1600–1637).
- **Why defect:** Prose and Task 13 Step 5 require `output/raw/transcript-<turn_id>.jsonl`, but the only paste-ready persist block never performs `copy2`. A subagent following the fence ships DB+zstd recoverability while skipping the SPEC hybrid raw on-disk path; Task 13 Step 5 can fail while hermetic/unit tests stay green.
- **Fix:** Add an explicit Step 2 sub-step with paste-ready `shutil.copy2` (after `_safe_relpath` on `transcript-{run_id}.jsonl`) inside the persist block, plus a hermetic test on the pure path helper or a grep guard for `transcript-{run_id}.jsonl` under `output_mnt / "raw"`.

### Finding 2A-2 — HIGH — Upload status poll lacks SPEC §4 job ownership
- **Location:** Task 9 Step 5 — `upload_status` returns `AsyncResult` state with no ownership check; SPEC §4 — *"mirror … `job_index` … polled via `/status/{job_id}/`"*.
- **Why defect:** Locked design E1 mirrors `batch_upload`, whose status handler calls `user_owns_job` after `register_job` on enqueue (`batch_upload/views.py:106`, `:318`). Plan never registers CC upload jobs or checks ownership — any authenticated user who guesses a Celery task UUID can poll another user's upload progress/result metadata (filename list in `meta.saved`).
- **Fix:** In Task 9 Step 5, after `run_cc_upload_task.delay(...)`, call `register_job(request.user.pk, task.id, project.id)` (reuse `batch_upload.job_index`); in `upload_status`, return 404 when `not user_owns_job(request.user.pk, job_id)`. Add hermetic tests mirroring `batch_upload/tests/test_views.py` ownership cases.

### Finding 2A-3 — HIGH — Task 11a has no paste-ready persist writer
- **Location:** Task 11a — Interfaces describe `_append_cc_turn_complete` / `serialize_cc_chat_log_entry`; Steps 1–2 only name them, no implementation body.
- **Why defect:** Global Constraints require Task 11a committed before Task 11 Step 2. A cold implementer must invent the RMW `extra_state` pattern (`chat_log` + `cc_traces` mirror + `CCSessionTranscript` upsert + `assistant_reply` key) with no paste-ready contract — high risk of wrong key (`reply`), in-place mutation, or missing transcript upsert.
- **Fix:** Paste full `_append_cc_turn_complete` and `serialize_cc_chat_log_entry` (including `compress(raw_jsonl)` upsert) into Task 11a before Step 2, matching Global Constraints' canonical `extra_state` write pattern.

### Finding 2A-4 — MEDIUM — `_turn_start_ts` not in Task 11 Step 1 checklist
- **Location:** Task 11 — *"At **turn start** (before container spawn): `translator._turn_start_ts = time.time()`"* appears only in prose; Step 1 checklist covers `_newest_jsonl_under` helpers and kwargs extension only.
- **Why defect:** Live `cc_engine.run_cc_turn` creates `CCStreamTranslator()` at `:499` with no `_turn_start_ts`. Persist block relies on `min_mtime=turn_start - 1` to pick the post-turn jsonl. Without an explicit Step 1 insertion point before `containers.run`, implementer uses persist-time `time.time()` or omits the field — `_newest_jsonl_under` can select a stale pre-turn jsonl on resume/multi-jsonl trees, yielding wrong traces/transcript blobs while live gate may still pass superficially.
- **Fix:** Add Task 11 Step 1 sub-step: immediately after `translator = CCStreamTranslator()`, set `translator._turn_start_ts = time.time()` before spawn; add source grep guard or hermetic test.

### Finding 2A-5 — MEDIUM — Task 13 commits `DEPLOY.md` with no authoring steps
- **Location:** Task 13 Step 9 — `git add … DEPLOY.md`; File Structure lists DEPLOY.md under Modify; no prior task defines Step-3 deploy notes (migration 0007, Celery task registration check, `build:embedded`, zstandard).
- **Why defect:** PLAN-7 explicitly sequences DEPLOY.md merge on post-Step-3 hash. Step 13 says "commit DEPLOY.md" but not what to append — operator procedure drift or empty commit omission.
- **Fix:** Add Task 13 Step 3b (or Step 8 pre-commit): bullet list of required DEPLOY.md additions (migrate command, celery inspect line from Task 9 Step 3b, frontend build command, `:pre-step3` snapshot note).

### Finding 2A-6 — MEDIUM — Owner-scoping tests missing vs SPEC §12
- **Location:** SPEC §12 — *"Owner-scoping: download/recover endpoints reject a non-owner"*; Task 10 — only `test_cc_endpoint_guards.py` traversal helper; endpoints *"live in Task 13"*.
- **Why defect:** No hermetic seam tests non-owner rejection (pure helper mocking queryset, or documented Django test exception). Task 13 live gate is manual prose — lazy implementer can ship owner filter typos until production.
- **Fix:** Add pure tests for session-owner guard helpers (e.g., `assert_session_owned(user, session_id)` returns false for wrong user) used by download/recover actions, or a source guard that both endpoints contain `ChatSession.objects.filter(user=request.user`.

---

## 2B — Stress Test

### Finding 2B-1 — HIGH — Most likely failure: reload-empty activity panel
- **Location:** Task 11 / 11a ordering + Task 12 Step 2 hydration.
- **Why defect:** Risk register rank 1 is accurate. Partial wiring (persist in wrong module, `reply` vs `assistant_reply`, or `AppLayout` not attaching live `ccTraces`) produces a panel that works on WS but empties on reload — exactly the defect Step 3 exists to fix.
- **Fix:** Already partially addressed by 11a ordering; add Task 13 Step 6 assertion template in `live_gate_transcript.txt` requiring JSON snippet of `GET session?include=turns` showing non-empty `cc_traces` after reload (not just UI prose).

### Finding 2B-2 — HIGH — Catastrophic: upload status IDOR + missing Celery registration
- **Location:** Task 9 Step 3b vs Step 5 `upload_status`.
- **Why defect:** If worker import is forgotten, uploads 202 then hang forever (silent I/O regression). If job_index skipped, cross-user job polling leaks upload filenames/progress.
- **Fix:** Task 13 Step 4 must include both `celery inspect registered | grep cc_assistant.upload` and a negative ownership poll test (second user gets 404 on first user's `job_id`).

### Finding 2B-3 — MEDIUM — Hidden dependency: AppLayout lacks artifact plumbing today
- **Location:** Task 12 Step 5 — wire `onCcArtifactDownload` from `EmbeddedApp`/`AppLayout`; live `AppLayout.tsx` ChatPanel has no `onArtifactDownload` prop (EmbeddedApp does at `:224`).
- **Why defect:** AppLayout `query_complete` also omits artifacts/ccTraces attachment. Implementer fixing EmbeddedApp only leaves AppLayout users unable to download CC artifacts or see activity — partial Step 3 delivery.
- **Fix:** Task 12 Step 5 explicit checklist: AppLayout must pass `onCcArtifactDownload` through ChatPanel → MessageBubble; mirror EmbeddedApp's artifact/`ccTraces` live-update pattern in AppLayout `query_complete`.

### Finding 2B-4 — MEDIUM — Rollback ambiguity on persist re-raise policy
- **Location:** Task 11 — *"**Always re-raise** on persist failure until Task 13 live gate passes"*.
- **Why defect:** After deploy, a transient DB error turns successful CC turns into `query_error` for users. Plan does not say when to soften to log-and-continue post-gate.
- **Fix:** Add post-Task-13 note: after live gate sign-off, downgrade to log + emit partial `query_complete` (or explicit "pause and ask" criteria).

### Finding 2B-5 — MEDIUM — Coverage target non-enforcement
- **Location:** Global Constraints — *"Pure modules … require **≥95%**"*; Task 6 Step 5b — coverage *"optional"*.
- **Why defect:** Stress path: implementer skips `--cov-fail-under=95` on several tasks, ships thin partition/zip edge coverage; declared Phase 2 bar becomes optional.
- **Fix:** Make `--cov-fail-under=95` mandatory in every listed task's verify command (remove "optional" in Task 6); add one sentence that failure blocks commit.

---

## 2C — Validate External Dependencies

### Finding 2C-1 — OK — `zstandard`, pydantic v2, orjson, Celery app path
- **Location:** Dependency Validation table; Task 1 `stream_reader` bomb guard.
- **Status:** `0006_merge_extra_state_guards` exists on disk; `CCStreamTranslator` and `_handle_result` at cited lines confirmed; Celery import path `nextseek_api.batch_upload.celery_app` matches live code. No blocking external API mismatch found.

### Finding 2C-2 — MEDIUM — SPEC §4 `job_index` pattern not validated in plan
- **Location:** SPEC §4 vs Task 9 implementation.
- **Why defect:** Not a PyPI issue — plan diverges from locked SPEC's established Django/filesystem dependency (`batch_upload.job_index`) without escalation note.
- **Fix:** Align Task 9 with SPEC §4 or add explicit plan-side deviation note with security acceptance test.

### Finding 2C-3 — LOW — `zstandard>=0.25` pin unverified on target image Python
- **Location:** Task 1 Step 5.
- **Why defect:** Must-verify on rebuild; unlikely blocker.
- **Fix:** Task 13 Step 1 add `python -c "import zstandard; print(zstandard.__version__)"` inside container to evidence file.

---

## 2D — Gameproof

### Finding 2D-1 — HIGH — Task 11 success = "helpers + regression" without transcript copy oracle
- **Success condition (quoted):** Task 11 Step 4 — *"Run `test_newest_jsonl_under_*` + regression suite"*; Step 2 persist block is prose + partial paste.
- **Cheapest fake:** Implement `on_turn_complete` chat_log append with hardcoded empty `cc_traces: []`, skip jsonl read/copy/DB upsert; unit tests pass; Task 13 Step 6 fails unless evidence is faked.
- **Remedy:** Add hermetic test for `serialize_cc_chat_log_entry` including non-empty `cc_traces`; grep guard that `copy2` + `CCSessionTranscript.objects.update_or_create` appear in `cc_engine.py` or `_append_cc_turn_complete`; Task 13 reload JSON assertion (2B-1).

### Finding 2D-2 — HIGH — Task 9 success = validator tests only
- **Success condition (quoted):** Task 9 Step 4 — *"PASS (validator cases). The Celery task body is exercised live in Task 13."*
- **Cheapest fake:** Ship validator + DRF actions but omit `celery_app.py` import (Task 9 Step 3b) or `register_job`; hermetic suite green.
- **Remedy:** Task 9 Step 6 add import/grep guard: `cc_upload_tasks` imported in `batch_upload/celery_app.py`; optional hermetic test that `upload_status` source references `user_owns_job`.

### Finding 2D-3 — MEDIUM — Task 10 endpoint success = import check only
- **Success condition (quoted):** Task 10 Step 3 — *"PASS (all). Endpoint behavior verified live (Task 13)."*
- **Cheapest fake:** Paste actions with owner filter typo (`session_id=session` without user) or `decompress(blob)` without `max_bytes` (SPEC §10 bomb bound); traversal tests pass.
- **Remedy:** `recover_transcript` must call `decompress(bytes(row.blob), max_bytes=row.uncompressed_size + slack)`; add owner-guard unit tests (2A-6).

### Finding 2D-4 — MEDIUM — Task 12 frontend methods unspecified
- **Success condition (quoted):** Task 12 Step 6 — *"npm run test" … Expected: PASS*.
- **Cheapest fake:** Stub `uploadFiles`/`downloadCcArtifact` as `Promise.resolve()`; Vitest mocks pass; no real URLs.
- **Remedy:** Paste `NextseekApiService.uploadFiles` → `POST …/cc-assistant/upload/`, `pollUpload` → `GET …/upload/status/{job_id}/`, `downloadCcArtifact` → `GET …/artifacts/{session}/download/?key=` matching Task 9/10 routes; Vitest asserts fetch URL shape.

### Finding 2D-5 — MEDIUM — Task 6 Step 5c acceptance validator gameable
- **Success condition (quoted):** *"replace check 16 `copier_published_scoped` … with turn-scoped validation"* — paste checks `"/" in key` only.
- **Cheapest fake:** Emit artifact keys `"foo/bar"` without matching on-disk layout; validator passes.
- **Remedy:** Rename check to `artifacts_turn_scoped`; assert keys start with `{turn_id}/` matching `query_task.task_id`; optionally stat `output/artifacts/{turn_id}/`.

### Finding 2D-6 — MEDIUM — Task 13 success partly prose
- **Success condition (quoted):** *"Success is met only if reload shows non-empty `cc_traces` … and upload Celery job completes"*.
- **Cheapest fake:** Markdown evidence index without committed `live_gate_transcript.txt` machine output.
- **Remedy:** Already hardened for Step 7 gate — keep requiring committed transcript; add mandatory JSON excerpt lines for `cc_traces` and upload `SUCCESS` result in that file.

---

## Non-blocking cosmetic notes

- Architecture header says *"Four new DRF `@action`s"* but Tasks 9/9b/10 add five (upload, status, list, download, recover).
- Phase 2 Vetting Log table references prior `.vetting/` filenames — informational only for orchestrator.
- Task 8 Step 7 stages two commits touching `cc_config.py` twice (grep-guard commit then E8 commit) — workable but slightly awkward.
- `[CONFIRM@PLAN]` items marked resolved in Self-Review; no remaining placeholders in task bodies.
