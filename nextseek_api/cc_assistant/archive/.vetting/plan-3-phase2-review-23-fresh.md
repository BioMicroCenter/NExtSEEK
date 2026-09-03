# Phase-2 Fresh Review (iter-23, cold-context, un-steered) — TARGET: PLAN-3-ui-based-io.md

Reviewer scope: the single artifact `nextseek_api/cc_assistant/archive/PLAN-3-ui-based-io.md`, judged as a cold-start
execution contract against the locked design SPEC-3 (authority), the predecessor specs (Step 1b/1c/2),
the successor PLAN-7, and the actual cc_assistant/services source. All four canonical lenses applied.
Independence preserved: no `.vetting/` prior-review file was read.

**Verification performed (empirical, not trust-the-prose):**
- Read every reused source symbol the plan claims. All exist with matching signatures (see 2A).
- Branch-by-branch compared the original `cc_summary._tool_use_line` (`:87-101`) against the plan's
  refactored `classify_tool_use` + new `_tool_use_line` — output is **byte-identical** on every branch
  (bash/write/edit/multiedit/notebookedit/read/skill/task/other, incl. empty-string and missing-key
  edge cases). The 1c-memory no-regression claim (Risk #3) holds.
- Ran the Task-7 coverage gate empirically: `models_api` imports hermetically (stdlib + pydantic only)
  and measures **96%** (237 stmts, 9 missed = the 3 unrelated `@field_validator` bodies) from a Turn-only
  test. The `--cov-fail-under=95` floor is reachable exactly as claimed.
- Probed the one external dependency: `zstandard 0.25.0` is installed; `ZstdDecompressor.stream_reader`
  bounded-read works as Task 1's `decompress` uses it (bomb guard raises, round-trip byte-identical,
  empty round-trips). All four Task-1 tests would pass against the pasted implementation.
- Confirmed the PLAN-7 start-gate marker allowlist (PLAN-7:132) is **byte-identical** to PLAN-3 Task 13
  Step 8: `Applying nextseek_api.0007` OR `[X] 0007_ccsessiontranscript`; `cc_assistant.upload`; `cc_traces`.
  PLAN-3 Task 13 Step 8 contracts the producing commands (migrate + showmigrations + celery inspect +
  the `?include=turns` excerpt). The cross-target handshake is satisfied.

---

## 2A — Vet (permissions / paths / reuse targets exist as named)

No substantive findings. Catalogued and confirmed present against live source:

- `cc_provision.py`: `slugify_project`, `_validate_segment` (regex `^[A-Za-z0-9._@+-]{1,128}$` admits
  `"42-px"`/`"alice"`), `project_dirname`, `ProjectIdentity(.id/.title/.slug/.dirname)`,
  `UserDirs` (frozen dataclass; fields `input_src/shared_src/scratch_src/output_src/cc_state_src/
  scratch_mnt/output_mnt/cc_state_mnt/memory_mnt` — **no `input_mnt` yet**, Task 3 adds it),
  `build_user_dirs(paths, project_dirname, user_id, *, session_id=None)` (positional call in Task 9
  matches; Task 3 test `build_user_dirs(_paths(), "42-px", "alice", session_id="S1")` matches),
  `resolve_user_project`, `ProjectResolutionError`. The Task-3 add `input_mnt=f"{user_mount}/input"`
  uses the real local var `user_mount`; `input_src`/`input_mnt` assertions in the Task-3 test are
  arithmetically correct. **Only one `UserDirs(...)` construction site exists** (cc_provision.py:99,
  keyword args), so the additive field cannot break a positional constructor.
- `cc_config.py`: `CCPaths(host_user_root, user_root_mount)` + `from_env()`; the Task-3 test's
  `CCPaths(host_user_root=..., user_root_mount=...)` matches the dataclass exactly.
  `_DEFAULT_HOST_USER_ROOT = "/Users/taishajoseph/dmac-dev/users"` present (Task 8 E8 target).
- `cc_summary.py`: `parse_transcript` (`:46`), `ParsedTranscript.line_count`/`.turn_count`
  (`turn_count` = count of `type=="user"` records, matching the fixture's 3 user lines),
  `_tool_use_line` (`:87`), `_truncate`, `build_actions_view` (`tool_result` pairing at `:114`).
- `translate.py`: `CCStreamTranslator` (`:26`), `_handle_result` (`:130`), the success
  `return [("query_complete", {... total_cost_usd ...})]` at `:149-156`. Task-5 `_translator()`
  (`__new__` + set `session_id`/`_terminated`) is valid; both Task-5 tests avoid `_joined_reply`.
- `cc_engine.py`: live region `:572-588` matches the plan's ORDERING-INVARIANT note exactly
  (publish call `:573`, `event, data = terminal` `:579`, Dropbox block `:580-587`, `send_event` `:588`);
  `_publish_artifacts` (`:639`, current sig `(scratch_mount, output_mount, *, output_host_root, before)
  -> list[str]`), `_snapshot_tree` (`:610`, skips symlinks), `snapshot_before` (`:628`),
  `_safe_relpath` (`:632`), `run_cc_turn(..., run_id, paths, ...)` (`run_id` in scope for Task 11),
  `translator = CCStreamTranslator()` (`:499`, before `containers.run` `:504` — `_turn_start_ts`
  sub-step is feasible; no `__slots__` on the translator), module-top imports do **not** include
  `time`/`django`/`cc_trace`/`cc_summary` (Task 11 local-import rationale holds).
- `services/cc_assistant.py`: `CCAssistantViewSet` (`:114`), `_resolve_credentials` (`:142`),
  extra_state RMW pattern (`:65-72`), and — load-bearing — `cc_state_key = str(chat_session.session_id)`
  is set **unconditionally** on the CC branch (`:223`) before `run_cc_turn` (`:337`), so
  `dirs.cc_state_mnt` is never `None` inside the persist block (`Path(dirs.cc_state_mnt)` is safe).
  **`Response`, `status`, `action` are all imported at module top (`:25-27`)** → Task 9b's `upload_list`
  uses `Response` with no local import safely (only `ProjectResolutionError` needs the local import,
  which the iter-22 fix added).
- `services/assistant.py`: Turn projection at `:521-530`; `entry` is the chat_log entry; Task-7
  passthrough `cc_traces=entry.get("cc_traces") or None` contains the exact substring the Step-1 guard
  greps. Task-11a Step-3 `artifacts = entry.get("artifacts") or ...` correctly supersedes line 520 for
  CC turns (no bundle).
- `models_api.py`: `Turn` (`:122-138`), `model_config = ConfigDict(extra="forbid")`,
  `Optional/List/Dict/Any` imported (used by `artifacts`) → Task-7 `cc_traces` field paste compiles.
- `batch_upload`: `job_index.register_job(user_id, job_id, project_id, jobs_dir=None)` and
  `user_owns_job(user_id, job_id, jobs_dir=None)` match Task-9 calls; `celery_app.app = Celery("batch_upload")`
  with `import …cc_sweep` anchor (`:54`) for Task-9 Step-3b; `BATCH_UPLOAD_MAX_TOTAL_BYTES`
  (dmac/settings.py:430) consumed via `getattr(settings, …, 200MB)`.
- `services/content_blobs.py`: `download_batch` (`:276`) + nested `_iter_and_cleanup` (`:359`) +
  `StreamingHttpResponse` — the Task-10 stream/zip pattern source is real.
- Migrations: `0006_merge_extra_state_guards.py` present, `0007` absent — Task-2 dependency correct.

Permissions table (hermetic pytest, makemigrations-no-migrate, Celery broker + `batch_upload` queue,
MEDIA_ROOT staging, host mount RW to `input_mnt`/`output`/`cc-state`, Django ORM + real migrate,
DRF owner-scoped endpoints, docker socket + rebuild, Playwright ≤$2, frontend Vitest/build, git on
`cc-step3-ui-io`) is complete and matches the per-task needs. No missing permission.

## 2B — Stress Test

- **Most likely failure mode (persist wiring stubbed):** closed hermetically by the two source-text
  grep guards in Task 11 Step 4 (`on_turn_complete(TurnCompletePayload(` in cc_engine;
  `on_turn_complete=_append_cc_turn_complete` in services) **plus** the Task 13 Step 6 non-empty
  `cc_traces`-after-reload assertion (a real DB read, not prose). Adequate.
- **Most catastrophic (paid turn lost / converted to error):** the persist block is best-effort on the
  success path behind `CC_PERSIST_STRICT` (default False); the only un-try/except statement that touches
  `dirs.cc_state_mnt` is safe because `cc_state_mnt` is provably non-None on the CC path (2A). A persist
  miss logs at error level and still delivers the paid reply. Sound.
- **Hidden dependencies:** Step-2 paths, 1b resume mounts, 1c `cc_summary` byte-identical output — all
  verified present/preserved. `diff_files` reuse is consistent with the pre-existing `_publish_artifacts`.
- **Ambiguous success conditions:** Task 13 conditions are scripted/committed (jq, `ls`, marker allowlist),
  not "I confirmed manually." See 2D.
- **Coverage risk:** every declared floor is empirically plausible; `models_api` (96%) and
  `cc_transcript_store`/`cc_trace`/`cc_artifacts`/`cc_provision`/`cc_upload_validate`/`cc_upload_list`
  are pure modules wired into per-task `--cov-fail-under=95`. The two declared exceptions are legitimate:
  Task 5 (`translate` is a pre-existing shared procedural module; touched lines proven by 2 assertions +
  live gate; whole-module gate unreachable in-scope) and the Task-9 `# pragma: no cover` defensive branch
  (provably unreachable once `/`,`\`,NUL,absolute are rejected — verified by reading the validator logic).
- **Rollback:** `:pre-step3` snapshot + `rollback.sh`; per-change sign-off on the running instance; the
  E8 default change and dead-config removal ship as isolated diffs. Risk register rows map to the right
  pause/revert actions.

## 2C — Validate External Dependencies

- **`zstandard ≥0.25`** — installed 0.25.0; `stream_reader` bounded-read verified by live probe. OK.
- **pydantic v2 unpinned** — ordered `Union[_Assistant,_User,_Other]` with `_Other` last + optional
  `type`; the fixture-driven Task-4 tests would go RED if smart-union mis-routed an assistant record,
  so the routing is test-guarded. OK.
- **orjson** — already the `parse_transcript` decoder. OK.
- **Celery `batch_upload.celery_app`** — anchor + explicit `cc_upload_tasks` worker import (Step 3b)
  present; `inspect registered | grep cc_assistant.upload` is the Task-13 gate. OK.
- **Vitest / `npm run build:embedded`**, **Django migration 0007 on 0006**, **Playwright live gate** —
  consistent with repo state; live gate is an accepted operational exception. OK.

No dependency risk that would derail execution.

## 2D — Gameproof

- **Pure-module tasks (1/3/4/6/7/9/9b/11-helpers):** each success condition is a real failing-then-passing
  hermetic test over real inputs with a coverage floor; a no-op/stub implementation fails the floor or the
  behavioral assertions. The Task-4 second fixture (Step 9b) closes extractor overfit. The Task-11a FIFO-cap
  mutation test (`range(10,60)` not `range(0,50)`) defeats a `chat_log[:50]` keep-oldest fake; the
  `apply_turn_to_extra_state` mirror test defeats a dropped E5 `es["cc_traces"]` write.
- **DB/HTTP/Docker tasks (9/10/11/11a/12):** no hermetic seam by design; the acceptance oracle is the
  Task-13 committed live gate — `ls` of `input/` (defeats an `os.replace` EXDEV fake and a stubbed task),
  non-empty `cc_traces` after **reload** (defeats swallowed persistence), two-turn same-basename download
  (defeats a non-turn-scoped key), and the PLAN-7-re-greppable committed transcript markers (defeats a
  fabricated/empty transcript). The unavoidable residual — a human could hand-fabricate
  `live_gate_transcript.txt` — is mitigated as far as a live gate allows (committed, secret-scanned, real
  ≤$2 spend, independently re-greped by PLAN-7's start-gate). Not a closable defect.
- **Dropbox removal (Task 8):** grep guard scans `cc_engine.py` + `services/cc_assistant.py` for both the
  reply string and `artifacts_published`, and asserts the neutral default is present — defeats "moved to a
  comment."

No new gameable condition found that the plan does not already close.

---

## Non-blocking cosmetic notes (NOT grounds for non-acceptance)

1. `cc_config.py` "neutral default" is at line **16**, not `:15` as cited in several places
   (`_DEFAULT_HOST_USER_ROOT`). Pure label drift; the grep guard keys off string content, not line number.
2. Task 13 step numbering is out of order (Step 0, 1, **3b**, 2, 3, 4 …) — Step 3b ("append DEPLOY notes")
   precedes Step 2 ("build frontend"). Content is unambiguous; only the ordinal sequence reads oddly.
3. SPEC-3 §6.2's comment `SCHEMA_VERSION = "3/trace-v1"  # mirrors cc_summary.SCHEMA_VERSION` is slightly
   inaccurate (`cc_summary.SCHEMA_VERSION` is `"1c/v1"`). The plan's `cc_trace` defines its own constant,
   so there is no functional coupling — purely a stale comment in the spec, not the plan.
4. Task 12 (frontend) is more prose-driven than the backend tasks (e.g. "insert near the composer button
   row"), but it is gated by Vitest + the Task-13 live UI gate and ships paste-ready `chatApi` methods,
   the CCActivityPanel test, the download branch, and the 3e handler — sufficient scaffolding.

---

## Verdict rationale

After deep cross-checking of every load-bearing claim against live source (signatures, line regions,
byte-identical refactor), empirical measurement of the one non-trivial coverage floor (models_api = 96%),
a live probe of the sole external dependency (zstandard 0.25.0 bounded decompress), and confirmation of the
PLAN-7 successor marker handshake, I find **zero substantive (CRITICAL/HIGH/MEDIUM) defects**. The plan is an
executable, TDD-first, gameproof contract a cold-start implementer can run end-to-end without questions. Only
cosmetic notes remain.

FINAL VERDICT: UNCONDITIONAL_ACCEPTANCE
