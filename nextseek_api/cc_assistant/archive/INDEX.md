# Archived cc_assistant documentation

Everything in this directory is kept for provenance only. None of it describes
current behaviour and none of it is maintained. For how the boundary works
today, read the `nextseek_api/cc_assistant/README.md` + `CLAUDE.md` pair beside
the code.

These documents are actively misleading if read as current. Several announce
themselves as live state: `PLAN-3-ui-based-io.md:3` is headed "TRUE STATE" as of
a date months past. That framing is exactly why they were moved here.

Nothing was deleted. Every file arrived by `git mv`, so `git log --follow` still
reaches its full history.

## Specs and plans

| File | What it covered | Why it is historical | Superseded by |
|---|---|---|---|
| `SPEC-1b-cc-resume.md` | Step 1b: core Container-CC `--resume`, per-session `.claude` persistence and multi-turn context. | Design-stage document ("awaiting user review") for work that shipped. Deliberately scoped to exclude the memory tier, so it describes only half of what now exists. | Nothing, the work shipped; the `README.md` + `CLAUDE.md` pair documents the resume path as it is. |
| `SPEC-1c-cross-session-memory.md` | Step 1c: distilled per-user memory, raw transcripts on demand, and the `fresh_session` flag layered on 1b. | Design-stage document for work that shipped, and the memory subsystem was later reconciled with a second implementation (issue #9), so its architecture is not the one in the tree. | Nothing, the work shipped; `README.md` + `CLAUDE.md` for `decide()` and the history feed. |
| `SPEC-2-multi-user-provisioning.md` | Step 2: project-stratified per-user directories, replacing the flat single-user layout. | Design-stage document for work that shipped, and its host-bind model was afterwards retired: `DMAC_USER_ROOT` gave way to the `dmac-cc-users` named volume with per-user Engine-API subpath mounts. | Nothing, the work shipped; `docker/nextseek.env.example` records the retirement, `README.md` + `CLAUDE.md` the current mounts. |
| `SPEC-3-ui-based-io.md` | Step 3: UI-based I/O, upload, output split, activity panel, Dropbox removal, session id. | Design-stage document for work that shipped and has since been reworked in the UI. | Nothing, the work shipped; `README.md` + `CLAUDE.md`, and `chat_frontend/README.md` for the UI half. |
| `SPEC-7-compose-native-prod-deploy.md` | Step 7: making full Container-CC deployable from the repo alone with compose plus gitignored operator secrets. | Design-stage document for work that shipped. Its evidence-path convention is still honoured by the tests, but the deploy procedure it specifies has moved on. | `nextseek_api/cc_assistant/DEPLOY.md` for the live procedure; `README.md` + `CLAUDE.md` for the boundary. |
| `SPEC-dev-merge-and-e2e-verification.md` | Merging `feat/dmac-assistant-full-integration` into `origin/dev`, plus a full-UI E2E verification. | A one-time merge design pinned to specific commits (`d3837a6`, `c5f23e7`, merge-base `935f5fa`). The merge happened; the branches and its re-pin rule no longer mean anything. | Nothing, the merge completed. |
| `SPEC-fix-internal-baseurl-and-0007-fk-heal.md` | Two defects found on the integration branch: the internal self-call base URL, and a `0007` FK charset heal. | Both fixes shipped. Deliberately minimal in scope, so the follow-ups it flagged out of scope were handled elsewhere. | Nothing, the fixes shipped. |
| `PLAN-2-multi-user-provisioning.md` | Task-by-task plan for Step 2, including `cc_provision.py` and the removal of the four old `CCPaths` roots. | Executed. Its host-path model was later retired (see `SPEC-2` above). | Nothing, the work shipped. |
| `PLAN-3-ui-based-io.md` | Task-by-task plan for Step 3, executed on `cc-step3-ui-io`. | Executed, merged and deployed. Its "TRUE STATE" banner is pinned to 2026-07-01 and is the single most misleading line in this directory. | Nothing, the work shipped; `README.md` + `CLAUDE.md`. |
| `PLAN-7-compose-native-prod-deploy.md` | Task-by-task plan for Step 7, including the acceptance-evidence convention `acceptance_evidence/step7/<run_id>/`. | Executed. Its evidence convention survives it and is still cited by `tests/test_cc_realstack.py:63`. | `nextseek_api/cc_assistant/DEPLOY.md` for deploying; the convention itself is live in `tests/acceptance_evidence/step7/`. |
| `LIVE_EVIDENCE.md` | Record of one deployment: the dmac_assistant integration image recreated onto the dev box on 2026-06-26, with the OI-3 agent-isolation evidence captured during a real CC turn that day. | Never meant to stay current. Its 57-test figure is now roughly 1497 test functions under `tests/`, and its screenshot paths are one operator's home directory. Its lines 8-10 describe a `seek_db_user` CREATE-grant gap on one box at one moment that readers keep mistaking for a standing constraint. | Nothing supersedes the record itself; the evidence artifacts it points at still live at `nextseek_api/cc_assistant/tests/live_evidence/`, and `CLAUDE.md:79-82` carries the warning about its grant-gap lines. |

## `archive/.vetting/`

65 automated review-iteration logs from the Step 3 and Step 7 plan executions.
They are machine output: successive adversarial review passes and fix logs
emitted by the vetting loop, superseded by the merged code the moment it landed.

Do not read them for current behaviour, do not cite them, and do not maintain
them. They are indexed here as a set, deliberately, and not one by one.
