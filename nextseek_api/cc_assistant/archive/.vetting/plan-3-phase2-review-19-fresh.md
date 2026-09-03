# Phase 2 Fresh Review (iter-19) — TARGET: PLAN-3-ui-based-io.md

Cold-context adversarial review against SPEC-3 (locked E1–E10, §6.2 enriched schema),
the live cc_assistant source, sibling Step-2 primitives, and the Step-7 consumption contract.
Verified the plan's reuse claims against actual code (cc_summary, translate, cc_engine,
cc_provision, cc_config, services/cc_assistant, models_api, services/assistant, batch_upload,
docker-compose.yml).

Overall: the plan is unusually well-grounded — file:line anchors match reality, the Task 5
`_tool_use_line` rewire is byte-identical to the current implementation (1c non-regression
holds), Turn `extra="forbid"` confirmed, `cc_run_id == str(query_task.task_id)` so the
transcript recover/turn_id contract is internally consistent, and Step-7's required evidence
file + content markers are produced by Task 13. One HIGH runtime defect and three MEDIUM gaps.

---

## 2A — Vet (permissions / paths / endpoints / external surfaces)

**[HIGH] Task 9 — staged-upload move crosses a filesystem boundary; `os.replace` will raise on the real deployment.**
Location: Task 9 Step 3, `cc_upload_tasks.run_cc_upload_task`:
> `os.replace(f["tmp_path"], dst)`  — moving `MEDIA_ROOT/cc_upload_staging/...` → `Path(input_mnt)/safe`.
Why it's a defect: The view stages into `stage_root = os.path.join(MEDIA_ROOT, "cc_upload_staging")`.
Verified `dmac/settings.py:90 MEDIA_ROOT = "/media"` and `docker-compose.yml` has **no `/media`
volume** → `/media` lives on the container overlayfs. The destination `input_mnt` resolves under
`/dmac/users` which **is** a host bind mount (`docker-compose.yml:28 - /srv/dmac/users:/dmac/users`).
`os.replace` is `rename(2)` and cannot move across devices → guaranteed
`OSError: [Errno 18] Invalid cross-device link` on **every** upload in this topology. Because the
Celery task body has no hermetic test (celery-gated, live-only), this surfaces only at the Task 13
$2 live gate — and the headline §4/3a upload feature is then dead. This pattern is NOT mirrored from
`batch_upload` (grep shows batch_upload performs no cross-tree move; the cross-device move is newly
introduced by Step 3's stage-then-move design).
Fix: replace `os.replace(...)` with `shutil.move(...)` (copy+unlink fallback across devices), OR
stage inside the same filesystem as `input_mnt` (e.g. `Path(input_mnt).parent/".upload_staging"`).
Add a note that the `finally: os.unlink(tmp_path)` then becomes a no-op for the moved file (FileNotFoundError already swallowed).

Permissions catalogue (all legitimate, resolved in the plan's Permissions table): hermetic pytest via `uv run`;
`makemigrations` (no migrate); Celery broker + `batch_upload` queue + explicit worker import; `MEDIA_ROOT`
staging write; host `DMAC_USER_ROOT` mount writes (`input_mnt`, `output/artifacts`, `cc-state` jsonl);
ORM + real migrate; owner-scoped DRF endpoints; docker socket for deploy; Playwright ≤ $2; npm test/build.
No missing credential/env/endpoint surfaced beyond the move-target filesystem issue above.

---

## 2B — Stress Test

**[MEDIUM] Task 11a — the FIFO cap (50) logic is unguarded by any test, hermetic or live.**
Location: Task 11a Step 1, `_append_cc_turn_complete`:
> `if len(chat_log) > MAX_CC_CHAT_LOG_TURNS: chat_log = chat_log[-MAX_CC_CHAT_LOG_TURNS:]`
Why it's a defect: `_append_cc_turn_complete` lives in `services/cc_assistant.py`, which imports Django
at module top, so it is **not importable** under the `--noconftest` no-Django hermetic harness. Only the
pure `serialize_cc_chat_log_entry` was extracted to the neutral `cc_turn_complete.py`; the cap + RMW stay
inline and untestable hermetically. The Task 13 live gate proves "reload shows non-empty cc_traces" but
never exercises the 50-turn boundary. A mutation `chat_log[:50]` (keep oldest) instead of `chat_log[-50:]`
(keep newest) passes every check in the plan. No-op/mutation test both go undetected.
Fix: extract the append+cap into a pure helper in `cc_turn_complete.py`
(`def append_capped(chat_log: list, entry: dict, *, cap: int = 50) -> list`) and unit-test newest-kept;
`_append_cc_turn_complete` then calls it. Cheap, removes the blind spot.

**[MEDIUM] Task 6 Step 6 / Task 11 — the `run_cc_turn` post-publish reorganization is under-specified and unguarded; literal paste NameErrors.**
Location: Task 6 Step 6 ("In `cc_engine.py` (`:573`), consume the dict …") pastes
`result = _publish_artifacts(...)` immediately followed by `if event == "query_complete":`.
Why it's a defect: in the live code `event, data = terminal` is assigned at **line 579, AFTER** the
publish call at 573 (verified). Pasting the `if event == "query_complete":` block at the `:573` anchor
references `event`/`data` before they exist → `NameError`. The correct arrangement keeps the unpack first,
then the result-consume block, then the Task 11 persist block, then `send_event`. Tasks 6 and 11 BOTH add
blocks here and must interleave in one order. No hermetic test executes `run_cc_turn`'s body
(`test_cc_engine_publish.py` tests `_publish_artifacts` directly), so a misordering is caught only at the
$2 live gate. Fix: state explicitly that the publish call stays before `event, data = terminal` (it only
produces `result`), and the `if event == "query_complete":` consume block + the Task 11 persist block both
go **after** line 579, replacing the old Dropbox block (580–587); show the final assembled order once.

**[MEDIUM] Task 11 — re-raise on the success path turns a paid, successful CC turn into a user-visible `query_error` with the reply lost, if jsonl discovery fails.**
Location: Task 11 persist block + "Empty/missing jsonl policy":
> `else: raise RuntimeError("cc persist: missing transcript jsonl after successful turn")`
Why it's a risk: the raise happens before `send_event(event, data)`; it propagates to
`run_cc_turn`'s outer `except Exception` (cc_engine.py:594) which emits `query_error`. So a turn that
actually produced a reply + artifacts (and incurred spend toward the $2 cap) shows the user an error and
the reply is discarded. The 3×200 ms retry mitigates the common race, but any path mismatch (e.g.
`dirs.cc_state_mnt / "projects"` layout drift, or `min_mtime=turn_start-1` excluding a slow-flushed file)
makes EVERY CC turn fail despite success. The plan documents this as intentional "no silent degrade … until
Task 13 live gate passes," but ships it with no task to soften post-gate and no fallback to still deliver the
reply. Fix: confirm this is the user's accepted prod behavior; OR send the `query_complete` (with reply +
artifacts) first and surface the persist failure as a non-fatal warning event, logging at error level so the
gate still fails loudly without destroying paid output. At minimum add an explicit decision marker.

Most-likely failure mode: upload cross-device OSError (HIGH above) → silent 202 then job FAILURE.
Most-catastrophic: a persist/jsonl path mismatch (MEDIUM above) making all CC turns error after paid work.
Hidden dependencies: `/media` overlayfs vs `/dmac/users` bind-mount boundary (now surfaced); `MAX_TURNS`
parity with `chat_nextseek/chat_memory.py` (cap value asserted nowhere). Rollback conditions are covered
by the Risk Register (atomic revert of the cc_engine handler; `rollback.sh`).

Coverage risk / exception adjudication (lens 2B-5):
- **Task 5 translate coverage exception — LEGITIMATE (PASS).** `translate.py` is a pre-existing procedural
  module; Task 5 adds two keys to `_handle_result`'s success return and no new module. A whole-module ≥95%
  floor would force testing unrelated handlers (`handle`/`finalize`/`_handle_system/_assistant/_user`,
  uncovered lines 58/68/97/104/123 verified outside the touched branch). The two added keys ARE guarded by
  two falsifiable, mutation-sensitive assertions (`test_result_surfaces_num_turns_and_duration`,
  `test_missing_meta_is_none_not_crash`) plus the Task 13 live gate that runs the real component. This meets
  the lens bar ("justified exception paired with a non-deferrable gate running the real component"). Keep as-is.
- **Task 9 `# pragma: no cover` on the `base != name` belt-and-suspenders line — LEGITIMATE.** Provably
  unreachable after `/`, `\`, NUL, absolute are rejected; the pragma keeps the floor honest. Fine.
- Tasks 11/11a/13 having no hermetic seam is acknowledged; the FIFO-cap MEDIUM above is the one coverable
  seam being skipped (fix via DI extraction).

---

## 2C — Validate External Dependencies

- **zstandard ≥0.25 (Task 1).** `ZstdDecompressor.stream_reader` bounded reads used for the bomb guard —
  valid public API. Round-trip + `max_bytes` cap tests are real. OK.
- **pydantic v2 (unpinned).** The ordered `Union[_Assistant, _User, _Other]` with `_Other` last + optional
  `type` is sound. Note: pydantic v2 default union mode is "smart," not strict left-to-right — but resolution
  is still correct because `_Assistant`/`_User` use `Literal` exact matches (higher score) than `_Other`'s
  `type: str`; well-formed `user`/`assistant` records bind to `_User`/`_Assistant` (load-bearing for the
  `tool_result` status pairing in `test_action_from_diff_and_status_from_tool_result`). No discriminated-union
  pin dependency. OK.
- **orjson** — already used by `cc_summary.parse_transcript` (verified `cc_summary.py:59`). OK.
- **Celery `batch_upload.celery_app`** — `app` import + explicit `import …cc_upload_tasks` in `celery_app.py`
  after the existing `import …cc_sweep` (verified celery_app.py:54). Registration path correct. OK.
- **Step-2 primitives** — `UserDirs`, `build_user_dirs(paths, project_dirname, user_id, *, session_id)`,
  `ProjectIdentity.id/.dirname`, `resolve_user_project`, `ProjectResolutionError`, `CCPaths(host_user_root,
  user_root_mount)/from_env`, `_resolve_credentials`, `job_index.register_job/user_owns_job` — all verified to
  exist with the signatures the plan calls. `input_src` present, `input_mnt` correctly additive. OK.

---

## 2D — Gameproof

Reviewed each task's success condition + cheapest fake (the plan's own Gameability Audit is solid; additions below):

- **Task 9 (validator tests pass).** Cheapest fake already noted by the plan (broken celery body / missing
  worker import) is closed by Step 3b + Task 13 Step 4. BUT the HIGH `os.replace` bug is itself a "passes
  hermetic, dies live" gap the plan does not anticipate — the validator tests never touch the move. Closing
  the move (2A fix) removes the trap.
- **Task 11a (chat_log append).** Success condition "grep + Task 13 reload hydration" does NOT cover the FIFO
  cap (2B MEDIUM). Mutation `chat_log[:50]` is a free fake. Remedy: pure-helper extraction + unit test.
- **Task 13 (panel survives reload).** Success condition is well-hardened: requires committed
  `evidence/3-ui-based-io-live/live_gate_transcript.txt` with a scripted `jq` JSON excerpt showing non-empty
  `turns[*].cc_traces` + `chat_log`-backed `assistant_reply` (not prose). Residual (inherent to a live gate):
  the transcript file could be hand-authored — acceptable for a non-re-runnable live gate, and Step-7's
  validator independently re-checks committed content markers (see Step-7 note below). No new remedy required.
- **Task 4 (fixture tests).** Anti-overfit second fixture (`cc_transcript_multitool.jsonl`, WebFetch+Read) is
  required in Step 9b; `test_envelope_counts… == 6` self-corrects the "`#` filename line written into the
  fixture" trap (line_count would become 7 and stay RED). Adequate.

Step-7 consumption check (per SIBLING CONTEXT): Task 13 Step 9 commits
`nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt` — exactly the path PLAN-7
Task 1 gates on (`live_gate_transcript_committed`, verified PLAN-7:86/89/98). PLAN-7's validator (PLAN-7:132)
also requires content markers `migrate nextseek_api 0007`, `cc_traces`, `inspect registered`, exit-code lines.
Task 13 PRODUCES all four (Step 3 migrate command, Step 6 cc_traces excerpt, Step 0 `inspect registered`
command, Step 8 "saved stdout/stderr + exit codes for every command"). Contract satisfied. See LOW note to
make the allowlist explicit.

---

## Non-blocking cosmetic / LOW notes

- **[LOW] `_Other.type: str | None = None` deviates from SPEC §6.3's literal `_Other.type: str`.** The plan
  documents this in Self-Review §2 and it is the *correct* fix (`{"_type":"unparsed"}`/blank lines carry no
  `type`; the SPEC's required-`str` would raise ValidationError and crash `extract_trace` on any real
  transcript with a blank line). §6.3 is illustrative parsing pseudocode, not the LOCKED §6.2 schema, so this
  is an acceptable documented deviation guarded by `test_unknown_record_type_does_not_crash`. No change needed
  beyond the existing note.
- **[LOW] Task 13 does not explicitly enumerate PLAN-7's content-marker allowlist.** It produces the markers
  incidentally; adding one line to Step 8 ("ensure `live_gate_transcript.txt` contains `migrate nextseek_api
  0007`, `cc_traces`, `inspect registered`, and per-command exit codes — PLAN-7 §8 markers") would harden the
  cross-target handshake.
- **[LOW] Task 13 "all" artifact-download branch re-zips `artifacts.zip`.** `download_artifact` key="all"
  does `art_dir.rglob("*")`, which includes the `artifacts.zip` written by Task 6 into the same `art_dir`,
  nesting the prior zip. Harmless (the default download path uses the explicit `{turn_id}/artifacts.zip` key),
  but a one-line `if p.name != "artifacts.zip"` filter would be tidier.
- **[LOW] Phase 2 Vetting Log** has a numbering gap (row 23 → 25, no 24). Cosmetic.
