# `nextseek_api/assistant/`

## What this is

The API half of the chat assistant: the ORM models every assistant table lives in, the Pydantic
wire models, the WebSocket consumer that streams a turn's progress, the adapters that persist a
turn, the OpenAPI prose, the bundle-to-spreadsheet export and the admin session inventory. It
handles no request itself: every HTTP route that reaches this code is an action on a ViewSet in
`nextseek_api/services/` (`assistant.py`, `cc_assistant.py`, `evaluator.py`, `nessie.py`).

The engine half left with the NessieAI move. The granular ops, their write gate, the reingest
helpers and the NS bundle projections are in `NessieAI/ns/` (`NessieAI/ns/README.md`); the route
decision is in `NessieAI/router/`. The HTTP contract for the granular ops stays here, in
`nextseek_api/assistant/CONTRACT.md`, because the models it specifies and the ViewSet that serves
it are on this side of the boundary.

It is not an installed Django app. Its models declare the parent app's label
(`app_label = "nextseek_api"` in each `Meta` in `nextseek_api/assistant/models_db.py`), so their
migrations live in `nextseek_api/migrations/`, and `nextseek_api/models.py` imports two of them so
that every class exists before the app registry is sealed.

## Surface

| Module | What it does |
|---|---|
| `models_db.py` | every assistant model: the `assistant_*` chat tables (`ChatSession`, `QueryTask`, the turn ledger, `CCSessionTranscript`) and the `eval_*` HiBayes tables |
| `models_api.py` | the chat request and response models, the SSE payloads, the error envelope, and the granular-op request and response models that `CONTRACT.md` specifies |
| `models_evaluator.py` | the models of the admin evaluator surface (`/nextseek_api/evaluator/`) |
| `consumers.py` | `TaskProgressConsumer`, the WebSocket behind `ws/assistant/progress/{task_id}/`; it polls the task row rather than a channel-layer group, so the in-memory channel layer does not constrain it |
| `routing.py` | the one WebSocket URL pattern, mounted by `dmac/asgi.py` |
| `pipeline_adapter.py` | `make_db_event_callback`: appends each progress event to `QueryTask.progress` and sets the terminal status |
| `session_adapter.py` | `DictSessionAdapter`: a `ChatSession` presented as the dict the engines expect; `save` merges bundle history under a row lock |
| `excel_export.py` | a stored bundle as inline tables and xlsx bytes (`extract_table_artifacts`, `build_tables_from_bundle`, `generate_table_xlsx`); shared with the admin project export |
| `session_debug.py` | an engine-agnostic inventory of one chat session for admin debugging: sizes and paths, never payloads |
| `session_export.py` | one chat session as the user sees it: `turn_rows`, the turn list behind `?include=turns`, and the streamed zip of the transcript plus every turn's files behind `GET /assistant/sessions/{sid}/download/` |
| `descriptions.py`, `descriptions_evaluator.py`, `descriptions_cc.py` | endpoint prose for the OpenAPI schema, held as module constants and read by `scripts/validate_viewset_conventions.py` |
| `CONTRACT.md` | the HTTP contract of the granular ops: op table, request and response models, auth, error envelope |

## Running and testing

The tests for this package are in `NessieAI/tests/api/` (the HTTP and WebSocket surface: consumer,
sessions, session adapter, excel export, session debug) and `NessieAI/tests/ns/` (the granular ops
behind `CONTRACT.md`). Their commands, and the local-settings overlay the lane needs, are in
`NessieAI/tests/README.md` ("Django lane").

## Depends on / depended on by

Depends on, outside this directory:

- `NessieAI/ns/bundle_download.py`, imported by `session_debug.py`, which also imports
  `NessieAI/cc/cc_transcript_store.py` lazily.
- `NessieAI/ns/artifacts.py` and `NessieAI/ns/debug_projection.py`, imported by
  `session_export.py`, which also imports `NessieAI/cc/cc_config.py`,
  `NessieAI/cc/cc_provision.py` and `nextseek_api/cc_assistant/cc_endpoint_guards.py` lazily.
- `channels` (`consumers.py`) and `openpyxl` (`excel_export.py`).
- The chat config object built by `startup/dev/lane_local_settings.py`, for the endpoint tests.

Depended on by (non-test importers):

- The ViewSets: `nextseek_api/services/assistant.py` (chat, sessions, downloads, granular ops),
  `nextseek_api/services/cc_assistant.py` (the router-dispatched endpoint),
  `nextseek_api/services/evaluator.py` and `nextseek_api/services/nessie.py`;
  `nextseek_api/services/project_export.py` takes `generate_table_xlsx`.
- `nextseek_api/cc_assistant/cc_endpoint_guards.py`, for the tables in `models_db.py`.
- ASGI boot: `dmac/asgi.py` is the only importer of `routing.py`, so the WebSocket exists only under
  the ASGI server.
- NessieAI engine modules that persist, through `models_db.py`: `NessieAI/cc/cc_transcript_store.py`,
  `NessieAI/router/turn_ledger.py`, `NessieAI/router/risk_overlay.py` and the `NessieAI/hibayes/`
  ORM modules (the allowed back-edges in `NessieAI/CLAUDE.md` "Boundary").
- The engine modules Phase B moved out of the services ViewSets: `NessieAI/cc/turn.py` (through
  `models_db.py`), `NessieAI/ns/turn.py` (`SessionSaveError` from `session_adapter.py`) and
  `NessieAI/ns/retry.py`, the evaluator's normalizers and retry body (through `models_evaluator.py`
  and `models_db.py`).
- `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py`, which imports `excel_export.py` lazily
  and behind a guard.
- The ns-sidecar, over HTTP: it calls the granular endpoints in `CONTRACT.md` and keeps a hand copy
  of the granular-op models (`NessieAI/docker/ns-sidecar/app/granular_models.py`).
- `ci/routes.py`, which declares the assistant routes CI owns.

See `nextseek_api/assistant/CLAUDE.md` for the invariants and traps.
