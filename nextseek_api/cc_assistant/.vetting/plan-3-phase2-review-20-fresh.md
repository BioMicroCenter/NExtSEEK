# Phase-2 fresh review (iter-20) — TARGET: PLAN-3-ui-based-io.md

Cold-context adversarial review. Authority hierarchy: locked SPEC-3 > PLAN-3 > task specs.
Verified every load-bearing reuse claim against live source (cc_summary.py, translate.py,
cc_engine.py, cc_provision.py, cc_config.py, services/cc_assistant.py, services/assistant.py,
assistant/models_api.py, batch_upload/{job_index,celery_app}.py, migrations/), and ran two
throwaway probes (pydantic ordered-union dispatch; models_api import coverage) and PLAN-7's
validator marker contract.

## 2A — Vet (can it execute without a hitch / permissions)

**[MEDIUM] Task 11 "Minimal persist block" references modules/symbols it never tells the
implementer to import.** Location: Task 11, "Minimal persist block inside run_cc_turn"
(`parsed = cc_summary.parse_transcript(raw) … trace = cc_trace.extract_trace(… ts=timezone.now().isoformat() …) … on_turn_complete(TurnCompletePayload(…))`).
Verified `cc_engine.py` top imports (lines 20–35) are: json, logging, os, re, shutil, threading,
Mapping, Path, Any/Callable, `.attach`, `.translate`, `.cc_config`, `cc_session`. It does **not**
import `cc_summary`, `cc_trace`, `django.utils.timezone`, or `TurnCompletePayload`. The plan is
elsewhere meticulous about new imports (e.g. Task 9 "Add `import os` and `import time` at module
top"), so this omission is out of character and load-bearing: the block `NameError`s at runtime,
and because no hermetic test ever executes `run_cc_turn`, the failure surfaces only at the Task 13
live gate (paid). Fix: enumerate the required imports (prefer **local** imports inside the block —
`from django.utils import timezone`, `from . import cc_summary, cc_trace`,
`from .cc_turn_complete import TurnCompletePayload` — so the hermetic `import cc_engine` in
test_cc_engine_publish stays Django-settings-free; `timezone.now()` reads `settings.USE_TZ` and
would raise ImproperlyConfigured if imported at module scope and exercised without settings).

**[LOW] Permissions are well catalogued.** The Permissions table + Risk Register + Dependency
Validation cover docker socket, Celery broker, MEDIA_ROOT staging, DMAC_USER_ROOT mounts, ORM
migrate, Playwright/≤$2. `makemigrations` (Task 2 Step 4) has a hand-author fallback for the
settings-import-on-box risk. No gap found. `register_job(user_id, job_id, project_id, jobs_dir=None)`
and `user_owns_job(user_id, job_id, jobs_dir=None)` signatures (batch_upload/job_index.py:25,80)
match the Task 9 paste exactly; latest migration is `0006_merge_extra_state_guards` so Task 2's
0007-on-0006 dependency is correct; `celery_app.py:54` imports `cc_sweep` so Task 9 Step 3b's
"after the cc_sweep import" is accurate.

## 2B — Stress Test

**[HIGH] Cross-target marker handshake is mis-specified — PLAN-3 names the marker substrings
PLAN-7's start-gate explicitly REFUSES to check, and omits two of the three it actually greps.**
Location: Task 13 Step 8: *"Ensure it contains the PLAN-7 §8 content-marker allowlist … :
`migrate nextseek_api 0007` (Step 3), `cc_traces` (Step 6 excerpt), `inspect registered` (Step 0),
and per-command exit-code lines (Step 8)."* PLAN-7 line 132 (the actual validator contract) greps
for **`Applying nextseek_api.0007`** (migration *stdout*, not the command), **`cc_assistant.upload`**
(registered-task name), and **`cc_traces`** — and states verbatim: *"Do **not** require the command
substrings `migrate nextseek_api 0007` or `inspect registered`… a legitimate already-committed
transcript may omit them… do **not** hard-require an `exit-code` substring."* So PLAN-3 directs the
implementer to ensure the two strings PLAN-7 will not look at, and never names the two
stdout markers PLAN-7 keys on. Failure mode: an implementer who satisfies PLAN-3's checklist (or who
re-runs migrate so it prints "No migrations to apply" instead of "Applying nextseek_api.0007…")
commits a transcript that passes PLAN-3 Step 8's self-check but is **rejected by PLAN-7 Task 1's
start-gate** — a defect discovered only at the downstream step, after Step 3 is marked done. Fix:
replace the Step 8 allowlist with PLAN-7's real one — `Applying nextseek_api.0007`,
`cc_assistant.upload`, `cc_traces` — and add a note that Step 3 must run the migration against a DB
where 0007 is **unapplied** so the `Applying nextseek_api.0007_ccsessiontranscript… OK` line is
actually emitted into saved stdout.

**[MEDIUM] The single most important wiring in Step 3 — the Turn projection passthrough — has no
hermetic guard and is gameable to a silent no-op.** Location: Task 7 Step 4 / Task 11a Step 3,
`services/assistant.py:521-529` add `cc_traces=entry.get("cc_traces") or None`. Verified: the two
Task 7 assertion tests construct `Turn(...)` **directly**; they never call the projection loop. The
Task 7 coverage gate is on `models_api` (a different module). So if the implementer forgets the
projection line, every hermetic test still passes and reload silently returns no traces — caught
only at the Task 13 live reload assertion. No-op test result: yes, the projection edit is unguarded
hermetically. Fix: add a small hermetic test that drives the chat_log→Turn projection with a fake
session/entry dict and asserts `cc_traces` rides through (the loop is pure over `extra_state["chat_log"]`;
a stubbed object with `.extra_state` and `.title` is enough), or explicitly accept the live-only
guard in the task's success condition.

**[LOW] Task 4 coverage floor (cc_trace ≥95%) leaves the `action="modified"` branch unexercised.**
Location: Task 4 — `test_action_from_diff_and_status_from_tool_result` asserts only `action=="created"`;
no test passes a non-empty `files_modified` that basename-matches a write/edit step, so the
`elif base in modified_base: action = "modified"` assignment is never executed. The module is small
enough that it likely still clears 95% (the second multitool fixture adds Read/WebFetch lines), but
this is the obvious line to dip below the hard `--cov-fail-under=95` gate. Fix: add one assertion
covering the modified-action path.

**Rollback conditions:** the Risk Register is thorough and correctly classifies persist-failure as
"pause and fix before deploy" with `rollback.sh` for migration issues. No gap.

## 2C — Validate External Dependencies

**[verified OK] pydantic ordered Union (Task 4).** Probe under the installed **pydantic 2.13.4**
confirmed `RECORDS = TypeAdapter(list[Union[_Assistant,_User,_Other]])` dispatches an assistant
record→`_Assistant`, user→`_User`, `{"type":"summary"}`→`_Other`, `{"_type":"unparsed"}`→`_Other`.
The unpinned-pydantic risk the plan calls out is real in principle (smart-union scoring could shift
in a future release) but does not block today. No change needed; the plan's mitigation note is
adequate.

**[verified OK] zstandard / orjson / Vitest / build:embedded.** `zstandard.ZstdDecompressor.stream_reader`
bounded read (Task 1 bomb guard) is correct API. `chat_frontend/package.json` confirms
`test = "vitest run"` and `build:embedded` exists (resolves SPEC §13 `[CONFIRM@PLAN]` for the build
command). No dependency risk found.

## 2D — Gameproof

**[HIGH] Internal contradiction + locked-design deviation on the `extra_state["cc_traces"]` mirror.**
Locations & the conflict:
- Locked **SPEC-3 E5** + **§6.5**: *"Persist: append the CCTrace … to `ChatSession.extra_state["cc_traces"]`"* (deeper authority).
- PLAN Task 11 item 5 bullet 2: *"Mirror append to `es["cc_traces"]` per locked E5 (mandatory, not optional)."*
- PLAN Task 11a header: *"Also mirror append to `extra_state["cc_traces"]` per locked E5 (both stores updated in one RMW save)."*
- PLAN Task 11a interface (same task, ~12 lines later): *"**do not** mirror into separate `es["cc_traces"]` (trace data lives in `chat_log[]` only…)"*
- PLAN Task 11a paste `_append_cc_turn_complete`: writes **only** `es["chat_log"]` + `CCSessionTranscript`; **no** `es["cc_traces"]` write.

The implementable artifact (the paste) silently omits the E5-mandated `extra_state["cc_traces"]`
write, contradicting the locked design AND three separate "mandatory" statements in the plan,
while one statement in the same task says don't. A cold implementer pasting the code ships the
non-mirroring version, "passing" the task while violating a mandatory instruction; one who reads the
prose stalls because the orders conflict. Functionally nothing currently reads `es["cc_traces"]` (the
projection reads `chat_log[].cc_traces`), so behavior is fine — but the plan is internally
inconsistent and deviates from the deeper authority without a clean, signed-off override. (Note the
plan elsewhere is careful to say "SPEC-3 §6.5/§7 lock the persist write path (E5/E6/E7)" — so it
acknowledges E5 is binding while the paste ignores it.) Fix: reconcile to **one** behavior — either
(a) add the `es["cc_traces"]` mirror to the paste to honor E5 literally, or (b) record an explicit
user-signed override that the chat_log entry supersedes E5's storage location and delete the three
"mandatory mirror" statements. Do not leave both.

**[MEDIUM] Fixture line-count trap (Task 4).** Location: Task 4 Step 5 fixture block, whose first
line is `# nextseek_api/cc_assistant/tests/fixtures/cc_transcript_sample.jsonl`, and
`test_envelope_counts_reuse_parsed_transcript` asserting `t.transcript_line_count == p.line_count == 6`.
Verified against `cc_summary.parse_transcript`: it keeps every non-empty line and maps an
unparseable line to `{"_type":"unparsed"}` while still counting it. The plan uses the identical
`# <path>` header convention for `.py` files (where it is a harmless comment that *is* written into
the file). If an implementer pastes the fixture literally including that header, the `.jsonl` has 7
lines → `line_count == 7`, the `== 6` assertion fails (RED stays RED), and a confused implementer may
"fix" it by weakening the assertion. Fix: state explicitly that the fixture is exactly the 6 jsonl
records with **no** `#` header line (same for the Step 9b multitool fixture).

**[LOW] Task 7 `--cov-fail-under=95` on models_api guards nothing about the task's intent.** Probe
confirmed `models_api.py` imports at **96%** (237 stmts, 9 missed = the three unrelated
`@field_validator` bodies for report-mode/submission-type) **without** the `cc_traces` field or
projection even being correct. So the coverage gate is a no-op oracle for Task 7 (it passes for a
stub). This is acceptable only because the two `test_turn_cc_traces` assertions do test the field's
behavior — but the gate itself is theater. No fix required; noting so it is not mistaken for real
protection (the real guards are the assertions + live gate).

**[verified legitimate] Declared coverage exceptions.** Task 5 (translate not whole-module gated):
the touched `_handle_result` lines ARE covered by the two new assertions; the uncovered
lines (58,68,97,104,123) are in sibling handlers outside the surgical scope — exception is
legitimate and paired with the Task 13 live gate. Task 9 `# pragma: no cover` on
`base != name or not base`: genuinely unreachable on POSIX once `/`,`\`,NUL,absolute are rejected
(`os.path.basename` only splits on `/`), so the pragma correctly measures reachable lines. Both PASS.

## Non-blocking cosmetic notes
- Task 12 cites `useMessages.ts:88` for the hydrate map; the hook starts at `:66` and the map is
  inside — minor line drift, symbol exists.
- `run_cc_turn`'s docstring still says "scoped Dropbox mounts … augmented with published host paths"
  (cc_engine.py:417-420); after Task 6/8 this is stale. Worth a one-line docstring refresh in the
  same edit, but cosmetic.
- The Phase-2 Vetting Log table skips iteration #24 (jumps 23→25); harmless numbering gap.
