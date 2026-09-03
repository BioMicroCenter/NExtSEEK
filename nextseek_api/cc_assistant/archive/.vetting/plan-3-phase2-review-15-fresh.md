# TARGET: `/home/taishajo/work/NExtSEEK/nextseek_api/cc_assistant/PLAN-3-ui-based-io.md`

## 2A — Vet

### Finding 1
- **SEVERITY:** HIGH
- **Location:** Task 11 ("Typed callback" + "Minimal persist block") + Task 11a (`_append_cc_turn_complete` paste in `services/cc_assistant.py`)
- **Quote:** "`on_turn_complete(TurnCompletePayload(...))`" inside `cc_engine.run_cc_turn`; "`def _append_cc_turn_complete(payload: TurnCompletePayload)`" in `services/cc_assistant.py` (which already has `from nextseek_api.cc_assistant import cc_engine` at module top).
- **Why it is a defect:** Constructing `TurnCompletePayload` inside `cc_engine.py` requires importing that symbol from `services/cc_assistant.py`, while `cc_assistant.py` already imports `cc_engine` at import time. The plan never places `TurnCompletePayload` in a neutral module (`cc_types.py`, `cc_turn_complete.py`, or `cc_engine.py`). A cold implementer following the paste blocks will hit a circular-import `ImportError` at Django startup.
- **Concrete fix:** Add `nextseek_api/cc_assistant/cc_turn_complete.py` (or similar) containing **only** the `@dataclass TurnCompletePayload` and `serialize_cc_chat_log_entry`. Import it from both `cc_engine.py` (construct payload) and `services/cc_assistant.py` (`_append_cc_turn_complete`). Explicitly forbid importing `TurnCompletePayload` across the `cc_engine` ↔ `services.cc_assistant` boundary.

### Finding 2
- **SEVERITY:** HIGH
- **Location:** Task 6 Step 5c ("Update `test_cc_realstack.py` + `validate_cc_acceptance.py`")
- **Quote:** Validator snippet reads `forced.get("artifacts")` from `forced_result.json`; existing realstack writer only dumps `"reply"`, `"error"`, `"total_cost_usd"` and still sets `published = data.get("artifacts_published")`.
- **Why it is a defect:** After Task 6 removes `artifacts_published`, the acceptance bundle will never contain `artifacts` in `forced_result.json`, and check 16 (`copier_published_scoped` on `published_files.json` host paths) becomes incompatible with turn-scoped artifact dicts. Hermetic acceptance (`test_cc_realstack.py` + `validate_cc_acceptance.py`) will fail or be "fixed" by deleting/weakening checks — letting a lazy implementer ship without turn-scoped artifact proof.
- **Concrete fix:** Paste-ready updates for both files: (1) extend `forced_result.json` dump with `"artifacts": data.get("artifacts")`; (2) replace `self.assertTrue(published)` with non-empty `artifacts` list + turn-scoped key assertions; (3) replace check 16 body entirely with the plan's `artifacts_turn_scoped` logic (or drop `published_files.json` and read only `forced_result.json`); (4) add a hermetic test in `test_validate_cc_acceptance.py` with fixture JSON containing turn-scoped keys.

### Finding 3
- **SEVERITY:** MEDIUM
- **Location:** Global Constraints ("Coverage targets (Phase 2 hardened)") vs Task 1/3/4/5/7/9 verify commands
- **Quote:** "Pure modules (Tasks 1, 3–7, 9 validator, 9b) require **≥95%** line coverage — append `--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95`"; Task 1 Step 4 verify command has no `--cov`.
- **Why it is a defect:** The coverage floor is declared globally but omitted from most task verify commands (only Task 6 Step 5b mentions `--cov`). An implementer can mark tasks done after unit tests pass while leaving pure modules at ~60% coverage; `--cov-fail-under=95` is never enforced in CI/hermetic runs.
- **Concrete fix:** Append the exact `--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95` invocation to every listed task's "Run tests" step (Tasks 1, 3, 4, 5, 7, 9 validator-only scope, 9b, 10 guards module).

### Finding 4
- **SEVERITY:** MEDIUM
- **Location:** Task 11 Step 1 ("Helpers only — paste-ready `_newest_jsonl_under` + tests")
- **Quote:** "Hermetic test: two jsonls with controlled mtimes; assert `min_mtime` picks the post-turn file." (no test file path, no paste-ready test body)
- **Why it is a defect:** Unlike Tasks 1–10, this load-bearing selector has no RED-step test code. `_newest_jsonl_under` is the sole guard against picking a stale cc-state jsonl; without a specified fixture test, implementers may skip it or write a trivial always-pass stub, letting wrong-jsonl persist slip through until the live gate.
- **Concrete fix:** Add `test_cc_newest_jsonl.py` with paste-ready tests: tmp tree with two `*.jsonl` files, controlled `os.utime`, assert `min_mtime=turn_start-1` returns the post-turn file and returns `None` when all candidates are older.

### Finding 5
- **SEVERITY:** MEDIUM
- **Location:** Task 12 Step 0 ("Extend `useChatApi`") + current `AppLayout.tsx` architecture note in Task 12 Step 5
- **Quote:** "AppLayout must use **one** `NextseekApiService` ref"; AppLayout today uses `useChatApi()` (internal `serviceRef`) **and** a separate `useState(() => new NextseekApiService(...))` for `useSessions` (verified at `AppLayout.tsx:42-63`).
- **Why it is a defect:** Step 0 adds `getAuthoritativeSessionId` / `apiService` to the hook but gives no paste-ready AppLayout refactor to remove the duplicate `useState` service and pass `apiService` into `useSessions({ service: apiService, ... })`. A implementer can wire `onCcArtifactDownload` to one instance while session hydrate/list uses another — 3e promotion and CC artifact download can diverge silently.
- **Concrete fix:** Add an explicit AppLayout Step 0 sub-step: delete `const [service] = useState(...)`, destructure `apiService` / `getAuthoritativeSessionId` from `useChatApi`, pass `apiService` to `useSessions`, and grep-guard that `AppLayout.tsx` contains exactly one `new NextseekApiService(`.

### Finding 6
- **SEVERITY:** MEDIUM
- **Location:** Task 9 Step 5 (`register_job` call)
- **Quote:** "`register_job(user_id=request.user.pk, job_id=task.id, project_id=project.id)`"
- **Why it is a defect:** `ProjectIdentity.id` is a **str** (`resolve_user_project` returns `id=str(projects[0]["id"])` or `personal-{user}`), but `register_job` is typed and documented for `project_id: int` (`job_index.py:25`). JSON job index will store inconsistent types vs `batch_upload`; future tooling comparing project IDs may break. Not caught by hermetic tests (upload list/ownership only checks `job_id`).
- **Concrete fix:** Coerce with an explicit policy in the upload action: `project_id=int(project.id)` when numeric, else `project_id=0` (or hash slug to int) — document the mapping; add a hermetic unit asserting the value passed to `register_job` is an `int`.

### Finding 7
- **SEVERITY:** MEDIUM
- **Location:** Permissions Required table + Task 9 / Task 13
- **Quote:** Celery broker + `batch_upload` queue listed for Tasks 9 and 13; no mention of **Redis/Rabbit broker reachability from the nextseek container**, Celery worker container co-deploy, or broker URL env vars.
- **Why it is a defect:** Upload returns HTTP 202 with `job_id` even when the worker is down or the task is `NotRegistered`. Task 13 Step 4 is the only proof upload completes; if broker/worker topology differs from dev assumptions, implementers stall without a preflight checklist (distinct from the in-process `celery_app` import fix in Task 9 Step 3b).
- **Concrete fix:** Add Task 13 Step 0 preflight: `celery -A nextseek_api.batch_upload.celery_app inspect ping`, `inspect registered | grep cc_assistant.upload`, and a smoke `delay()` + poll — fail deploy if any exit non-zero. Record broker host env (`CELERY_BROKER_URL` or project equivalent) in Permissions Required.

### Finding 8
- **SEVERITY:** LOW
- **Location:** Task 11 ("Failure policy" / empty jsonl)
- **Quote:** "`raise RuntimeError('cc persist: missing transcript jsonl after successful turn')`" before `send_event(query_complete)`
- **Why it is a defect:** A successful CC turn that cannot locate jsonl within 3×200ms retries surfaces as `query_error` / pipeline exception to the client — correct for the live gate but brittle in prod if cc-state flush is slow. Plan does not state post-gate relaxation or retry budget tuning.
- **Concrete fix:** Document post-Task-13 policy (keep fail-closed vs bounded retry/backoff); optionally add metric/log field `cc_persist_jsonl_miss` and extend retry to 5×500ms with Task 13 evidence requirement.

---

## 2B — Stress Test

### Finding 9
- **SEVERITY:** MEDIUM
- **Location:** Risk Register row 8 + Task 13 Step 6 success condition
- **Quote:** "Task 13 must assert non-empty `cc_traces` after reload"; success met only if "reload shows non-empty `cc_traces`".
- **Why it is a defect (ambiguous success):** Task 11a mirrors traces to both `chat_log[].cc_traces` **and** `extra_state["cc_traces"]`, but reload hydration reads **`chat_log` only** (`assistant.py:501-529`). An implementer can append to `es["cc_traces"]` alone (E5 mirror) with a broken `chat_log` writer and still pass a manual grep of `extra_state` while the UI reload gate fails — or conversely skip E5 mirror and pass reload. The live gate JSON excerpt requirement helps but does not name which store must be populated.
- **Concrete fix:** Task 13 Step 6 must assert `GET …/sessions/{id}?include=turns` returns `turns[*].cc_traces` non-empty **and** that the same turn exists in `chat_log` with `assistant_reply` + `cc_traces` (scripted jq check in `live_gate_transcript.txt`).

### Finding 10
- **SEVERITY:** MEDIUM
- **Location:** Task 11 + Task 6 coupling ("atomic" handler edit)
- **Quote:** "Tasks 6 and 8 both touch this handler — land the hybrid split **and** Dropbox removal atomically"
- **Why it is a defect (rollback):** Task numbering separates Task 6 (engine publish) and Task 8 (config/grep) across commits; only Step 6 sub-step mentions atomic Dropbox removal. A implementer doing Task 6 Step 5 publish rework in one commit and Task 6 Step 6 caller/Dropbox in another leaves intermediate broken states despite the coupling rule.
- **Concrete fix:** Merge Task 6 Steps 5–6 and Task 8 grep-dependent reply removal into a **single commit checkpoint** with an explicit "do not push intermediate commits between Step 5 and Step 6" fence.

### Most likely failure mode
Celery upload task not registered or wrong service instance in AppLayout → upload 202 with no files in `input/` (Task 9 + Task 12 dual-service gap).

### Most catastrophic failure mode
Owner-scoping bypass on artifact/transcript download (Task 10) — mitigated by guard helpers but endpoints not hermetically tested; cross-user leak if `ChatSession` filter regresses.

### Hidden dependencies
Live Celery worker + broker; Step-2 `resolve_user_project` SEEK API; Playwright forced-CC budget; per-change sign-off on running instance; PLAN-7 hard gate on committed `live_gate_transcript.txt` (sequencing OK in plan).

### Coverage risk
Declared 95% floor is non-trivial for `cc_trace.py` / `cc_artifacts.py` but unenforced in verify commands (Finding 3) — justified exception only for Tasks 10/11/13 DB+HTTP paths is reasonable if the floor is actually run.

### Rollback conditions
Plan correctly points to `rollback.sh` and tracker flip only after Task 13; persist re-raise (Finding 8) means partial deploy can fail turns loudly — pause, don't flip tracker.

---

## 2C — Validate External Dependencies

### Finding 11
- **SEVERITY:** LOW
- **Location:** Dependency Validation table (`zstandard`)
- **Quote:** "`ZstdDecompressor.stream_reader` supports bounded reads (Task 1 bomb guard) | OK"
- **Why it is a defect:** Verified via throwaway probe: the Task 1 streaming loop pattern works with current PyPI `zstandard`. However, the plan does not pin an upper bound (`zstandard>=0.25,<1`) — a future major release could change streaming semantics. Must-verify at image build time remains implicit.
- **Concrete fix:** Pin `zstandard>=0.25,<1` in Task 1 Step 5 and record the probed version in Task 13 evidence.

### Finding 12
- **SEVERITY:** LOW
- **Location:** Task 2 Step 4 migration dependency
- **Quote:** "`dependencies = [('nextseek_api', '0006_merge_extra_state_guards')]`"
- **Why it is a defect:** Verified on disk: `0006_merge_extra_state_guards.py` exists. No issue today. If parallel migrations land on the integration branch before Step 3 executes, `0007` number may collide — plan assumes sequential execution on `cc-step3-ui-io` only.
- **Concrete fix:** Task 2 Step 4: "run `makemigrations`; if 0007 taken, use next number but keep `db_table`/`app_label` guards unchanged."

### External deps status
| Dep | Status |
|-----|--------|
| pydantic v2 unpinned + ordered Union | OK — matches `cc_summary.py` patterns |
| orjson | OK — already in 1c |
| Celery `batch_upload.celery_app` | OK — `cc_sweep` import precedent at `celery_app.py:54` |
| Vitest / `npm run build:embedded` | OK — `package.json` has both |
| Playwright live gate | Accepted exception — Task 13 only |
| PLAN-7 shared files (`cc_config.py`, `cc_engine.py`, `DEPLOY.md`) | Sequencing consistent — Step 3 append DEPLOY first; PLAN-7 rewrites post-deploy |

---

## 2D — Gameproof

### Finding 13
- **SEVERITY:** HIGH
- **Location:** Task 11 Step 2 success path + Task 13 Step 6
- **Quote (success condition):** "Confirm the panel shows commands/files/num_turns live, then **reload the session** and confirm the panel is still populated"
- **Cheapest fake:** Implement `CCActivityPanel` + live WS attach of `cc_traces` on `query_complete`, but skip `_append_cc_turn_complete` / `chat_log` append (or write `reply` instead of `assistant_reply`). Live panel works; reload is empty — implementer closes Task 12/13 with Playwright screenshot of live-only state.
- **No-op test:** Task 11a grep test on `serialize_cc_chat_log_entry` passes without DB writer; Task 7 `Turn.cc_traces` pydantic test passes without persistence.
- **Mutation test:** Deleting `_append_cc_turn_complete` body leaves Tasks 4/5/7 green; only Task 13 reload JSON check catches it — strengthen Task 11a with a hermetic test that `serialize_cc_chat_log_entry` output matches keys `assistant.py` projection reads (`assistant_reply`, not `reply`).
- **Remedy:** Already partially in Task 11a; add mandatory Task 13 jq proof on `turns[].cc_traces` after reload (Finding 9).

### Finding 14
- **SEVERITY:** MEDIUM
- **Location:** Task 9 + Task 13 Step 4
- **Quote (success):** "Expected: PASS (validator cases). The Celery task body is exercised live in Task 13."
- **Cheapest fake:** Ship validator + DRF actions + `celery_app` import line without verifying worker picks up task; HTTP 202 returns `job_id` while files never land in `input/`.
- **Remedy:** Task 13 Step 4 must include host-path stat of uploaded file **and** `upload/list` returning basename (already mentioned) plus Celery task SUCCESS in poll response — record all three in `live_gate_transcript.txt`.

### Finding 15
- **SEVERITY:** MEDIUM
- **Location:** Task 10 Step 0 + Step 3
- **Quote:** "Endpoint behavior verified live (Task 13)"; hermetic scope is `resolve_artifact_path` + mocked `session_owned_by_user`.
- **Cheapest fake:** `download_artifact` always raises 404 for non-owner but implementer never tests owner-positive path; cross-user negative guard passes hermetic suite while happy-path download is broken until manual click.
- **Remedy:** Add Task 13 Step 5 assertion that downloaded bytes match scratch deliverable hash; add hermetic test building a tmp `artifacts/<turn_id>/file` tree and asserting `resolve_artifact_path` + file read (no Django).

### Finding 16
- **SEVERITY:** MEDIUM
- **Location:** Task 8 Step 1 grep-guard
- **Quote:** "`assert 'artifacts_published' not in (CC / 'cc_engine.py').read_text()`"
- **Cheapest fake:** Rename symbol to `artifactsPublished` or move Dropbox string to a constant in another module outside grep scope (`cc_assistant/` + `services/cc_assistant.py` only).
- **Remedy:** Extend grep-guard to scan `nextseek_api/cc_assistant/` **and** `nextseek_api/services/cc_assistant.py` for "Dropbox", "artifacts_published", "/Users/taishajoseph" (partially done); add case-insensitive "dropbox" scan across both trees.

### Finding 17
- **SEVERITY:** LOW
- **Location:** Task 4 Step 9b (anti-overfit second fixture)
- **Quote:** "Add second fixture … Prevents extractor overfit"
- **Cheapest fake:** Add multitool fixture file but duplicate assertions from primary fixture (copy-paste test body with different filename only).
- **Remedy:** Require distinct expected `kind` sequence in `test_multitool_trace_kinds()` (`read`, `tool`) — already stated; enforce in test paste block.

### Ranked gameable conditions
1. **Task 11/11a persist skip** (HIGH) — live-only activity panel
2. **Task 6 acceptance not updated** (HIGH) — weaken/delete check 16
3. **Task 9 Celery body unproven until live** (MEDIUM)
4. **Task 10 download happy path live-only** (MEDIUM)
5. **Coverage floor unenforced** (MEDIUM)

---

## Non-blocking cosmetic notes

- Task 13 Step numbering runs Step 1 → Step 3b → Step 2 (out of order); prose is clear enough.
- Task 13 Step 1 repeats `zstandard` add already required in Task 1 Step 5.
- Task 9b `upload_list` snippet omits `from rest_framework.response import Response` (obvious to implementer).
- File Structure section omits `cc_upload_list.py`, `cc_endpoint_guards.py`, `cc_turn_complete.py` (if added per fixes).
- Phase 2 Vetting Log references prior `.vetting/` files — orchestrator metadata only.
- `[CONFIRM@PLAN]` items marked resolved; `_handle_result` on `CCStreamTranslator` confirmed at `translate.py:130`.
