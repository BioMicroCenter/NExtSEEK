# `nextseek_api/assistant/`

## What this is

The assistant subsystem's **shared library**: the ORM models the whole chat stack
writes, the Pydantic wire contract two other codebases copy, the native
implementation of the granular sidecar ops, and the WebSocket consumer that
streams one turn's progress to the browser. It handles no request itself — every
HTTP route that reaches this code is an action on a ViewSet defined in
`nextseek_api/services/assistant.py`, registered at `nextseek_api/urls.py:38`.

It is not an installed Django app either. `INSTALLED_APPS` runs from
`dmac/settings.py:144` to `dmac/settings.py:180` and names the parent app at
`dmac/settings.py:178` and the Container-CC AppConfig at `dmac/settings.py:179`;
no element of that tuple names this package. Its thirteen model classes join the
parent app by an explicit label instead (`nextseek_api/assistant/models_db.py:23`
is one of the thirteen), so their migrations live in the parent's chain:
`assistant_chat_session` is created at
`nextseek_api/migrations/0001_initial.py:29`, `assistant_query_task` at
`nextseek_api/migrations/0002_querytask.py:32`, `assistant_turn_ledger` at
`nextseek_api/migrations/0010_turn_ledger.py:50` and `assistant_cc_transcript` at
`nextseek_api/migrations/0007_ccsessiontranscript.py:61`. The line that makes all
thirteen classes exist before the app registry is sealed is a single import in
the parent's models module, `nextseek_api/models.py:3`, which names two of them
and carries a suppression comment for the rest.

43 Python files, 25 of them under `tests/`, counted 2026-09-03 by a `find` for
`*.py` beneath the package and beneath its `tests/` directory.

## Surface

This is an ordinary Python package: the surface is its public callables and
constants plus the modules behind them, and the edge is imports. Two edges are
not imports at all and outrank most of the imports — an HTTP contract a separate
container calls, and a hand-maintained verbatim copy of one module inside that
container's source tree. Both appear under the dependency section.

See `nextseek_api/assistant/CONTRACT.md` for the op table, the request and
response models per op, the auth rules and the error envelope; that document is
the interface specification and nothing here duplicates it.

**Persistence — `models_db.py`.** Thirteen models under four `assistant_*` tables
and nine `eval_*` tables. `ChatSession` (`nextseek_api/assistant/models_db.py:7`)
carries two typed JSON columns plus the catch-all `extra_state`
(`nextseek_api/assistant/models_db.py:16`). `QueryTask`
(`nextseek_api/assistant/models_db.py:30-36`) is the async turn record every
progress event is appended to. `CCSessionTranscript`
(`nextseek_api/assistant/models_db.py:303-328`) stores one turn's compressed
Claude Code jsonl, and its docstring is the authority on which failures
deliberately persist nothing.

**Wire models.** `models_api.py` holds the chat request/response and SSE payload
models — `QueryRequest` at `nextseek_api/assistant/models_api.py:12-23`, the
rehydrated `Turn` at `nextseek_api/assistant/models_api.py:124-147`, the error
envelope at `nextseek_api/assistant/models_api.py:478-487` — and, below the
banner at `nextseek_api/assistant/models_api.py:229-240`, the granular-op models.
`models_evaluator.py` is the parallel set for the admin evaluator surface
(`nextseek_api/assistant/models_evaluator.py:1`).

**Granular ops — `granular.py`.** `run_op`
(`nextseek_api/assistant/granular.py:44-58`) dispatches through a table of nine
handlers (`nextseek_api/assistant/granular.py:261-271`). Seven are the ported
sidecar ops; `run-ls` (`nextseek_api/assistant/granular.py:192-211`) and
`build-upload-xlsx` (`nextseek_api/assistant/granular.py:214-259`) are the
NExtSEEK-only reingest pair added afterwards, whose two request models sit at
`nextseek_api/assistant/models_api.py:302-316`. Every `chat_nextseek` agent is
imported inside a handler body, never at module scope.

**The write gate — `write_gate.py`.** `build_gate`
(`nextseek_api/assistant/write_gate.py:78-100`) is the safety-critical control:
strict-`True` confirmation for `api-write`
(`nextseek_api/assistant/write_gate.py:86`), allowlist membership for `api-read`
(`nextseek_api/assistant/write_gate.py:92`), pass for the five read-class labels
(`nextseek_api/assistant/write_gate.py:34`), deny for anything else
(`nextseek_api/assistant/write_gate.py:99-100`). The allowlist is the bundled
`read_safe_endpoints.json`, resolved relative to this module at
`nextseek_api/assistant/write_gate.py:46-48`; the nine endpoints added by the
2026-07-06 read-safety audit are pinned as a regression list at
`nextseek_api/assistant/tests/test_granular_write_gate.py:79-94`.

**Reingest helpers.** `reingest_qa.qa_rows`
(`nextseek_api/assistant/reingest_qa.py:35-43`) grades composed rows
CLEAN/SOFT_FLAG/HARD_REJECT, reading ancestors out of every key containing
"parent" rather than the literal one
(`nextseek_api/assistant/reingest_qa.py:55-61`).
`upload_workbook.render_upload_workbook`
(`nextseek_api/assistant/upload_workbook.py:22-27`) emits the four sheets the
batch-upload parser needs, and the round trip back through that parser is the
stated correctness oracle
(`nextseek_api/assistant/upload_workbook.py:1-13`).

**Progress transport.** `nextseek_api/assistant/routing.py:7-11` declares the one
WebSocket pattern;
`dmac/asgi.py:23` imports it and `dmac/asgi.py:25-29` mounts it behind channels'
auth stack. `TaskProgressConsumer`
(`nextseek_api/assistant/consumers.py:18`) polls the task row rather than
subscribing to a channel-layer group — the interval is at
`nextseek_api/assistant/consumers.py:44` and the loop at
`nextseek_api/assistant/consumers.py:136-158` — which is why the in-memory
channel layer configured at `dmac/settings.py:184-188` is not a constraint on it.
`pipeline_adapter.make_db_event_callback`
(`nextseek_api/assistant/pipeline_adapter.py:10-16`) is the writing half: it
appends each event to the JSON column and sets terminal status
(`nextseek_api/assistant/pipeline_adapter.py:30-44`).

**Session state — `session_adapter.py`.** `DictSessionAdapter`
(`nextseek_api/assistant/session_adapter.py:17-32`) presents a `ChatSession` as
the dict the engine expects, splitting two typed keys
(`nextseek_api/assistant/session_adapter.py:14`) from everything else. `save`
takes a row lock and merges bundle history by id
(`nextseek_api/assistant/session_adapter.py:89-118`), for the reason set out at
`nextseek_api/assistant/session_adapter.py:58-72`.

**Read-side projection.** `debug_projection.bundle_debug_entries`
(`nextseek_api/assistant/debug_projection.py:125-131`) rebuilds the Search Details
panel from a stored bundle because the live events were never persisted
(`nextseek_api/assistant/debug_projection.py:1-18`), and it always states
truncation (`nextseek_api/assistant/debug_projection.py:97-99`).
`excel_export.py` turns a bundle into inline tables and xlsx bytes:
`extract_table_artifacts` at `nextseek_api/assistant/excel_export.py:259-266`,
`build_tables_from_bundle` at `nextseek_api/assistant/excel_export.py:145-151`,
`generate_table_xlsx` at `nextseek_api/assistant/excel_export.py:375`,
`generate_search_xlsx` at `nextseek_api/assistant/excel_export.py:424`, with the
inline row cap at `nextseek_api/assistant/excel_export.py:50`.

**OpenAPI copy.** `nextseek_api/assistant/descriptions.py:1-5`,
`nextseek_api/assistant/descriptions_evaluator.py:1-5` and
`nextseek_api/assistant/descriptions_cc.py:1` hold endpoint prose as module
constants. Two of the three are scanned by the conventions validator listed at
`scripts/validate_viewset_conventions.py:22-26`; the assistant ViewSet itself is
excluded from the generated schema at `dmac/openapi_hooks.py:14-16`.

**Isolated app config — `task6_app.py`.** An `AppConfig` that mounts this
package's models under the parent label for one disposable acceptance database
(`nextseek_api/assistant/task6_app.py:9-16`), referenced only from
`nextseek_api/eval/task6_settings.py:8`.

**`tests/acceptance_evidence/` is committed state, not scratch.** Six files: two
dated run bundles, each a README plus the captured stdout and the machine-recorded
token ledger of a paid real-stack run. The parent bundle records that an earlier
run existed only in gitignored scratch and is therefore not verifiable, and that
this one is the committed re-run
(`nextseek_api/assistant/tests/acceptance_evidence/README.md:6-15`); the dated
subdirectory is the post-merge repeat
(`nextseek_api/assistant/tests/acceptance_evidence/2026-06-25-post-merge/README.md:38-39`).
No code reads them: a grep for `acceptance_evidence` across the tree returns, for
this package, only those files themselves and prose in `DEPLOYMENT.md`, with every
programmatic reader belonging to `nextseek_api/cc_assistant/`. They are evidence
for a reader, and the test that regenerates them is the artifact that matters.

## Running and testing

One free lane, one paid lane, and no marker-based split: a grep for `host_only`
over the package returns nothing, so nothing here is excluded from an
in-container run.

**Free lane.** Executed on this branch on 2026-09-03: 302 collected, of which 288
passed, 6 failed, 8 skipped, and 14 subtests passed, in 10.03 seconds, with no
database server, no network and no spend. The 8 skips are the paid tests
declining themselves. Of the 6 failures, 5 report a drift in committed state that
originates outside this package and 1 is an artifact of the container the lane
runs in.

The lane depends on a gitignored settings overlay. What it supplies is the
chat-config object built at `startup/dev/lane_local_settings.py:19`, which no
committed settings module defines; without it, measured on 2026-09-03, 19 of these
tests fail on a missing settings attribute long before any assertion. The container shape it uses,
including the two `schema_rag` directories the settings module insists on, is the
recipe documented for local use at `ci/gate/live_routes.py:16-27`.

**Paid lane.** `nextseek_api/assistant/tests/test_granular_realstack.py:101` gates
the eight real-stack tests behind an environment flag read at
`nextseek_api/assistant/tests/test_granular_realstack.py:33`, with a hard spend
cap at `nextseek_api/assistant/tests/test_granular_realstack.py:37`. It needs a
running stack, a SEEK login valid on that stack, and real provider keys, so it is
the lane the committed evidence directory exists to stand in for. (not run)

## Depends on / depended on by

Depends on, outside this directory:

- `chat_nextseek`, for every agent the granular ops call — imported only inside
  handler bodies, at `nextseek_api/assistant/granular.py:62`,
  `nextseek_api/assistant/granular.py:98-99`,
  `nextseek_api/assistant/granular.py:148-149`, and eight further such lines sit
  in other handler bodies, thirteen in all as counted on 2026-09-03 by grepping
  this package's non-test `.py` files for an import naming that package.
  No line matching an import of `chat_nextseek` at the start of a line
  exists in any `.py` file in this package outside `tests/`, so importing this
  package never drags the engine in.
- `nextseek_api.batch_upload.helpers.collect_parent_tokens`, imported at MODULE
  scope by `nextseek_api/assistant/reingest_qa.py:13`, defined at
  `nextseek_api/batch_upload/helpers.py:30-38`. That module imports SQLAlchemy at
  `nextseek_api/batch_upload/helpers.py:8`, so this is the heaviest import edge in
  the package.
- `channels`, at module scope in `nextseek_api/assistant/consumers.py:12-13`, and
  `django.urls` in `nextseek_api/assistant/routing.py:3`.
- `openpyxl`, at module scope in `nextseek_api/assistant/excel_export.py:15` and
  `nextseek_api/assistant/upload_workbook.py:19`.
- `startup/dev/lane_local_settings.py:19` for the chat-config object the endpoint
  tests read off Django settings.

Depended on by. Non-test importers grouped by kind; the many test modules that
import this package are left out.

- Production request path. `nextseek_api/services/assistant.py:89-105` pulls in the
  dispatcher, the gate, the models, the projection and both adapters;
  `nextseek_api/services/cc_assistant.py:41-44` takes the same models and adapters
  for the Container-CC route; `nextseek_api/services/evaluator.py:31-54` takes the
  evaluator models, descriptions and both adapters;
  `nextseek_api/services/project_export.py:44` takes the workbook writer.
- ASGI boot. `dmac/asgi.py:23` is the only non-test importer of
  `nextseek_api/assistant/routing.py:7-11`, so the WebSocket exists only under the
  ASGI server.
- Container-CC internals. `nextseek_api/cc_assistant/turn_ledger.py:4`,
  `nextseek_api/cc_assistant/risk_overlay.py:8`,
  `nextseek_api/cc_assistant/cc_transcript_store.py:69` and
  `nextseek_api/cc_assistant/cc_endpoint_guards.py:17` all reach into
  `models_db.py` for the tables this package owns.
- The op registry, which reads the allowlist FILE rather than importing the gate:
  `nextseek_api/cc_assistant/op_registry/ops.py:19` binds the path at module scope
  and `nextseek_api/cc_assistant/op_registry/derive.py:16` binds it again.
- The evaluation store. Eleven non-test modules under `nextseek_api/eval/` import
  `models_db.py`, seven of them at module scope, counted 2026-09-03 —
  `nextseek_api/eval/export.py:4`, `nextseek_api/eval/generation_validation.py:6`,
  `nextseek_api/eval/judge_cache.py:7`, `nextseek_api/eval/paid_run_state.py:7`,
  `nextseek_api/eval/spend_conservation.py:9`,
  `nextseek_api/eval/run_authorization.py:14` and
  `nextseek_api/eval/generation_store.py:14`.
- The vendored engine, one way only: `chat_nextseek` imports this package at
  `chat_nextseek/src/chat_nextseek/orchestrator.py:1012`, lazily and inside a
  function, to reuse the artifact extractor. Nothing in this package imports the
  orchestrator.
- The ns-sidecar container, over HTTP rather than by import. It POSTs to a path
  built at `docker/ns-sidecar/app/ns_client.py:97`, health-checks itself against
  the identity endpoint at `docker/ns-sidecar/app/healthcheck.py:21`, and carries a
  hand-maintained verbatim copy of `models_api.py` declared as such at
  `docker/ns-sidecar/app/granular_models.py:1-6`. Its own gate keeps only the
  confirmation check and delegates the read allowlist here
  (`docker/ns-sidecar/app/write_gate.py:1-3`,
  `docker/ns-sidecar/app/write_gate.py:14-15`).
- CI and gates, by path string. `build_tools/plan005_gate.py:28` pins one test
  module here as a named lane and `build_tools/plan005_gate.py:227` treats the
  whole `tests/` directory as gate-relevant; `ci/routes.py:531` onward declares
  the assistant routes CI owns, including the reingest workbook endpoint at
  `ci/routes.py:755-759`.
- Test harnesses. `nessie_tests/bundle.py:30` imports `models_db.py` lazily inside
  a reader function. `nessie_tests/sources.py:253` is NOT an import that file
  performs: it is probe source inside the string literal opened at
  `nessie_tests/sources.py:245` and executed inside the app container. Likewise
  `scripts/plan018_v4_9_task8_deploy.py:1642` sits inside the string returned by
  `scripts/plan018_v4_9_task8_deploy.py:1632-1633`.

See `nextseek_api/assistant/CLAUDE.md` for the invariants, the traps and the one
command.
