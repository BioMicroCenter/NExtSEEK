# NessieAI/ns/

The engine half of the old `nextseek_api/assistant/`: the native granular ops that the
`ns-sidecar` and the CC agent call, their write gate, and projections of NS result bundles.
No Django models live here; the HTTP contract and the ORM stay in `nextseek_api/assistant/`.

## Surface

| Module | What it does |
|---|---|
| `granular.py` | `run_op` dispatches a table of ten handlers: seven ported sidecar ops, `run-ls` and `build-upload-xlsx` for reingest, and `graph-schema`, which returns the live graph catalog as text so the CC agent reads the deployed graph instead of a snapshot baked into its image. Every `chat_nextseek` agent is imported inside a handler body |
| `write_gate.py` + `read_safe_endpoints.json` | `build_gate`: strict `True` confirms `api-write`; allowlist membership for `api-read`; pass for the read-class labels; deny anything else. The JSON is found beside the module |
| `reingest_qa.py` | `qa_rows` grades composed rows CLEAN, SOFT_FLAG or HARD_REJECT |
| `upload_workbook.py` | `render_upload_workbook` emits the four sheets the batch-upload parser reads |
| `bundle_download.py` | serves the files of a stored NS bundle |
| `debug_projection.py` | `bundle_debug_entries` rebuilds the Search Details panel from a stored bundle |
| `turn.py` | the NS chat turn: `run_sse_pipeline` and `run_async_pipeline`, the pipeline bodies of the classic `query` (SSE) and `query/async` endpoints, which run the orchestrator for the request's mode and save the turn (`_save_session_or_report`); `make_sse_send_event`; `_granular_args`, a granular request projected into `run_op`'s args. Also the helpers both engines share: `_select_chat_config` (the admin-only `use_prod` ChatConfig switch) and `_auto_title_if_unset` (titles a chat from its first query) |
| `artifacts.py` | report artifacts on disk: `_granular_outputs_dir` (a fresh run-root for an op that writes files) and `_safe_artifact_path` (serves a stored path only when it resolves inside `<BASE_DIR>/outputs` or `NEXTSEEK_OUTPUTS_DIR`) |
| `retry.py` | the evaluator's retry engine: `classify_path` (a mode and debug dict to the `(execution_mode, path_mode, path_subtype)` tuple), `normalize_from_task` and `normalize_from_bundle` (a `QueryTask` or a stored bundle to an `EvaluatorRetryContextResponse`, with `_build_retry_signals`), the run-list and retry lookups `_task_has_bundle` and `_find_bundle`, and `run_retry`, the pipeline body of `POST /nextseek_api/evaluator/retry/`. The orchestrator is imported lazily (`_get_orchestrator`). Not the dict-based functions of the same names in `chat_nextseek.evaluator.normalization` |

The HTTP contract for these ops (op table, request and response models, auth, error envelope) is
`nextseek_api/assistant/CONTRACT.md`. The ViewSet actions that call `run_op` are in
`nextseek_api/services/assistant.py`. That ViewSet keeps every HTTP and host seam (session
resolution, the `QueryTask` row, the event callback, the session adapter, credentials, the
thread start and the SSE stream) and hands them to `turn.py`; patch the orchestrator entry
points at `NessieAI.ns.turn`, where they are looked up.
The evaluator ViewSet in `nextseek_api/services/evaluator.py` does the same for `retry.py`:
it resolves the source run and the credentials, creates the `QueryTask`, builds the event
callback and the session adapter, and starts the thread that runs `run_retry`.

## Running and testing

Tests are in `NessieAI/tests/ns/` (engine) and `NessieAI/tests/api/` (HTTP surface); commands are in `NessieAI/tests/README.md`.

## Depends on / depended on by

- Depends on `NessieAI/chat_nextseek/` (agents, lazily; the config and orchestrator at import, from `turn.py`; the orchestrator lazily, from `retry.py`), `nextseek_api.batch_upload.helpers` (from `reingest_qa.py`), `nextseek_api.assistant.session_adapter` (`SessionSaveError`, from `turn.py`), and `nextseek_api.assistant.models_evaluator` plus `models_db` (`QueryTask`, from `retry.py`), all allowed back-edges.
- Called by `nextseek_api/services/assistant.py` (`granular`, `write_gate`, `turn`, `artifacts` and the bundle projections) and `nextseek_api/services/evaluator.py` (`retry`); `nextseek_api/assistant/session_debug.py` imports `bundle_download`; the Container-CC turn imports `turn`.
- `NessieAI/cc/op_registry/ops.py` reads `read_safe_endpoints.json` at import.
- `NessieAI/docker/ns-sidecar/` calls these ops over HTTP and keeps its own copy of the wire models.
