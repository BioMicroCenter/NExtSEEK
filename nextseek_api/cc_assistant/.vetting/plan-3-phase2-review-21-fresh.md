# Phase-2 fresh re-vet (iter 21) — TARGET: PLAN-3-ui-based-io.md

Cold-context independent review. Authority: SPEC-3 (locked E1–E10, §6.2 enriched) > PLAN-3 > task specs.
Verified the plan's reuse/extension claims against live source under `nextseek_api/`, the PLAN-7 marker
handshake, the frontend toolchain, and probed real pydantic-2.13 union behavior.

**Bottom line:** The plan is unusually accurate. Nearly every load-bearing claim checks out against the
running code (signatures, line numbers, scope, byte-identical refactor, coverage numbers, cross-plan
markers). Findings are MEDIUM/LOW hardening items, no CRITICAL/HIGH. Verdict: CONDITIONAL_ACCEPTANCE.

What I positively confirmed (so the next hardener does not re-litigate):
- `cc_config.CCPaths(host_user_root, user_root_mount)` + `_DEFAULT_HOST_USER_ROOT="/Users/taishajoseph/dmac-dev/users"` at the cited spot; Task 8/E8 replacement is real.
- `UserDirs`/`build_user_dirs` shape: `input_src`, `output_src`, `output_mnt`, `scratch_mnt`, `cc_state_mnt` all present; adding `input_mnt` after `input_src` is order-safe (all fields required, no defaults). Task 3 test math is correct (`/dmac/users/42-px/alice/input`).
- `_publish_artifacts` at `:639` with `(scratch_mount, output_mount, *, output_host_root, before) -> list[str]`; `_safe_relpath`, `_snapshot_tree`, `snapshot_before` exist; `run_cc_turn` defines `dirs`/`run_id`/`translator`/`before`/`scratch_mount`/`output_mount` in scope (call-site/persist-block references resolve).
- `translate._handle_result` success branch is exactly `[("query_complete", {reply, bundle_id:None, cc_session_id, total_cost_usd})]`; `CCStreamTranslator` at `:26`; the Task 5 `__new__` test path is valid (reply is a non-empty str so `_joined_reply` is not hit).
- `cc_summary.parse_transcript(raw: bytes)`, `ParsedTranscript.line_count/turn_count/records` (records are dicts; unparsed→`{"_type":"unparsed"}`; turn_count = user-record count). The proposed `classify_tool_use`/`_tool_use_line` rewrite is genuinely **byte-identical** to the current renderer across all branches (bash/write/edit/multiedit/notebookedit/read/skill/task/other), so 1c memory does not regress.
- `cc_session.store_has_transcripts` globs `Path(store_dir)/"projects".rglob("*.jsonl")`, and `services/cc_assistant.py:91` uses `Path(dirs.cc_state_mnt)/"projects"` — so Task 11's `Path(dirs.cc_state_mnt)/"projects"` root is correct (no `.claude/projects` mismatch).
- pydantic 2.13.4 smart-mode `TypeAdapter(list[Union[_Assistant,_User,_Other]])` resolves the fixture correctly to `_User/_Assistant/_Other(summary)/_Other(None)` even though `_Other` is an all-optional catch-all (probed live). Extraction tests will pass.
- `batch_upload.job_index.register_job(user_id, job_id, project_id, jobs_dir=None)` and `user_owns_job(user_id, job_id, jobs_dir=None)` match the call sites; `batch_upload.celery_app.app = Celery("batch_upload")` with `autodiscover_tasks(["nextseek_api.batch_upload"])` only — so Task 9 Step 3b's explicit `import …cc_upload_tasks` is genuinely required (correctly flagged).
- `Turn` has `model_config = ConfigDict(extra="forbid")`, imports `Any/Dict/List/Optional`; **measured** `--cov=nextseek_api.assistant.models_api` with a Turn-only test = **96% (237 stmts, 9 miss)** — Task 7's ≥95 floor is reachable as claimed.
- `services/assistant.py:521-529` projection matches the plan; the Task 7 guard substring `cc_traces=entry.get("cc_traces")` is present in the Step 4 paste.
- PLAN-7:132 allowlist is byte-identical to PLAN-3 Task 13 Step 8: migration marker (`Applying nextseek_api.0007` OR `[X] 0007_ccsessiontranscript`), `cc_assistant.upload`, `cc_traces`. The `showmigrations` idempotency fallback is present on both sides.
- Frontend: `vitest run` (`npm run test`), `build:embedded` → `outDir ../static/js/chat_assistant` all exist; `ArtifactFile {artifact_type:"file", key, label?, file_format}` matches the dicts `_publish_artifacts` emits. All referenced TSX/TS files exist (ReportArtifacts is under `src/components/ChatPanel/`).

---

## 2A — Vet (execution readiness / permissions)

**(LOW) Task 6 Step 5b — coverage gate wired to the wrong test file.**
Location: Task 6 Step 5b — "Coverage (mandatory): append `--cov=nextseek_api.cc_assistant.cc_artifacts --cov-fail-under=95`" onto a run of **only** `test_cc_engine_publish.py`.
Why a defect: the dedicated `test_cc_artifacts_split.py` (the real exerciser of `partition_changed`/`build_artifact_zip`/`RAW_PREFIX`) is run **without** `--cov` in Step 4; the ≥95 gate then rides on the engine-publish test, which only hits `cc_artifacts` transitively. If that test's tree doesn't traverse the `>1`-file zip path, the floor can fail spuriously and stall the implementer at the commit gate. (Contrast Task 3, which deliberately runs *all* provision test files to reach the floor.)
Fix: include `test_cc_artifacts_split.py` in the Step 5b `--cov=…cc_artifacts --cov-fail-under=95` command (run both test files together), mirroring Task 3's multi-file coverage command.

Permissions catalogue (no gaps found): hermetic `uv run` pytest (Tasks 1–7,9,9b); `makemigrations` no-migrate (Task 2); MEDIA_ROOT staging write + Celery `batch_upload` queue (Task 9); host mount write to `dirs.input_mnt`/`output/artifacts`/`cc-state` (Tasks 3/9/10/11) — consistent with the Step-2 RW mount Django already uses to mkdir scratch; Docker socket + image rebuild via the SA `docker:cli` helper (Task 13); Playwright ≤$2 (Task 13); git on `cc-step3-ui-io`. All present in the Permissions table.

## 2B — Stress Test

**(MEDIUM) Task 11 Step 1 contradicts the locked best-effort persist policy (re-introduces a previously-fixed regression if followed literally).**
Location: Task 11 Step 1 — "On missing jsonl after **3× 200ms retry, raise `RuntimeError`**." vs. the authoritative "Minimal persist block" + "Empty/missing jsonl policy (Phase 2 hardened — best-effort)": "The hard `RuntimeError(...)` is raised **only** when `CC_PERSIST_STRICT` is set."
Why a defect: the two passages prescribe opposite behavior. The unconditional raise is exactly the "paid reply converted to query_error on persist failure" defect iter-19 removed. A careful implementer who codes Step 1 literally (it is the first imperative step that mentions the raise) re-introduces it; only the later paste corrects it. Authority note: SPEC-3 §6.5/§7 lock the *write path*, not a re-raise-and-discard, so best-effort is the intended plan-level policy — Step 1's wording is stale.
Fix: reword Task 11 Step 1's last sentence to "On missing jsonl after 3× 200ms retry, deliver the reply without a persisted trace and log at error level; raise only under `CC_PERSIST_STRICT`," matching the Step 2 paste.

Most likely failure mode: persist wiring present but never invoked (see 2D-A) → reload-empty panels caught only by the paid gate. Most catastrophic: owner-scoping bypass on download/recover — adequately guarded (`ChatSession.objects.filter(user=request.user)` + `_safe_relpath` + `is_relative_to`). Hidden dependencies (Step-2 paths, 1c byte-identical summary, cc-state jsonl root) all verified consistent. Coverage risk: the declared exceptions (Task 5 translate no-floor; Task 9 `# pragma: no cover` on the provably-unreachable defensive branch; Tasks 11/11a/13 "no hermetic seam") are each legitimate and paired with the non-deferrable Task 13 live gate (which PLAN-7's start-gate hard-requires committed). Rollback conditions are concrete (`rollback.sh`, atomic cc_engine revert).

## 2C — Validate External Dependencies

No dependency defects. `zstandard>=0.25` `ZstdDecompressor.stream_reader` bounded-read bomb guard is real; pydantic v2 unpinned ordered-union behavior confirmed working on 2.13.4 (probed); orjson already in 1c; Celery `batch_upload.celery_app.app` import + explicit task registration confirmed necessary and correct; Vitest 4 + `build:embedded` present; migration 0007 dependency on `0006_merge_extra_state_guards` is plausible (the existing `0006_*` name is referenced; verify the exact predecessor filename at makemigrations time — the plan already offers a hand-author fallback).

## 2D — Gameproof

**(MEDIUM) The core deliverable (persist → reload survival) has NO hermetic or source guard on the actual wiring invocation.**
Success condition quoted (Task 13 Step 8): "Success is met only if reload shows non-empty `cc_traces` on the CC turn ... and upload Celery job completes." This is itself falsifiable and scripted (committed `jq` excerpt) — good.
Gap: the *only* check that `run_cc_turn` actually calls `on_turn_complete(TurnCompletePayload(...))`, and that `services/cc_assistant.py` actually passes `on_turn_complete=_append_cc_turn_complete` into `run_cc_turn`, is the paid ($2) live gate. I grepped the plan: there is a source guard for the chat_log `assistant_reply` key (Task 11a) and for the projection passthrough (Task 7 `test_projection_passes_cc_traces_through`), but **none** for the two wiring lines themselves.
- No-op test: if a lazy implementer adds the `on_turn_complete` kwarg but never invokes it (or never wires the callback from `services`), every hermetic test still passes (the pure helpers `append_capped`/`apply_turn_to_extra_state`/`serialize_cc_chat_log_entry`/`_newest_jsonl_under` are tested in isolation; the grep guards only check the *helper bodies* and the *projection read*, not that the engine writes). Only the live gate goes red.
- Mutation test: deleting the `on_turn_complete(...)` call in `cc_engine.py` corrupts no hermetic assertion.
Remedy (cheap, closes it before the paid gate): add two source-text guards (same pattern already used for `assistant_reply`/projection):
  1. in a Task 11 test, assert `cc_engine.py` source contains `on_turn_complete(TurnCompletePayload(`;
  2. in a Task 11/11a test, assert `services/cc_assistant.py` source contains `on_turn_complete=_append_cc_turn_complete`.
This is a hardening add, not a blocker on its own (the live gate is mandatory and non-deferrable), but it is the single cheapest defense against a "marked DONE, wiring stubbed" failure and the highest-value fix in this review.

Other gameproof angles checked and found adequately closed: Task 8 grep-guard scans whole `cc_assistant/` + `services/cc_assistant.py` (Dropbox-string-moved-to-comment closed); Task 4 second fixture (`cc_transcript_multitool.jsonl`) blocks extractor overfit; Task 2 model-shape guard is paired with the live migrate; Task 11a FIFO-cap and E5-mirror tests are mutation-sensitive (`[:50]`-keep-oldest and dropped-mirror both go red). Task 5's no-floor exception is legitimate (pre-existing procedural module, surgical 2-key change, proven by 2 assertions + live gate).

---

## Non-blocking cosmetic notes
- File Structure "Modify" list (line 62) omits `hooks/useChatApi.ts`, though Task 12 (line 2050) modifies it. Reconcile the inventory.
- Plan prose names `ReportArtifacts.tsx` without its path; it lives at `src/components/ChatPanel/ReportArtifacts.tsx`.
- `run_cc_turn` docstring still says "scoped Dropbox mounts ... reply augmented with published host paths" (line ~417) — stale once Task 6/8 land; harmless but worth updating in the touched commit.
- Phase-2 Vetting Log iteration numbering skips 24 (rows jump 23→25); cosmetic.
