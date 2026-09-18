# Native Assistant Granular-Ops Contract

The HTTP contract for the native granular ops: what the `ns-sidecar`, and through it the
Container-CC agent, calls to run one piece of the NS engine. The ops run in `NessieAI/ns/`
(`run_op` in `NessieAI/ns/granular.py`); the HTTP actions are on `AssistantViewSet` in
`nextseek_api/services/assistant.py`. The sidecar keeps a hand copy of the request and response
models in `NessieAI/docker/ns-sidecar/app/granular_models.py`.

All endpoints are **additive** to the existing `AssistantViewSet`; existing endpoint behavior is unchanged.

- **Base path:** `/nextseek_api/assistant/`
- **Auth:** same as the existing assistant endpoints: `TokenAuthentication`,
  CSRF-exempt `SessionAuthentication`, or `BasicAuthentication`; the caller must
  be `IsAuthenticated` **and** in a participating project
  (`UserInParticipatingProject`). For `api-read` / `api-write` the caller's
  NExtSEEK credentials (HTTP Basic, or session `username`/`password`) are
  injected into a per-request `ChatConfig` copy and used for the outbound call,
  exactly as `query`/`query_async` do.
- **Success envelope:** `200` with `{"op": "<op>", "result": { … }}`.
- **Error envelope:** `{"code": "<CODE>", "errors": [{"title": "<CODE>", "detail": "…"}]}`
  with the canonical code, so the thin client maps it to its CLI exit:
  `VALIDATION` (422), `WRITE_BLOCKED` (403), `AGENT_FAILED` (502),
  `CONFIG_ERROR`/`CONFIG_MISSING` (500). Unauthenticated → `401`; not in a
  participating project → `403`.
- **Models:** `nextseek_api/assistant/models_api.py`. Request models mirror the sidecar's
  `_ws_contract` arg schemas; response models are a typed `{op, result}` envelope over a lenient
  (`extra="allow"`) result.

## Op table

The dispatcher is `_HANDLERS` in `NessieAI/ns/granular.py`, and it holds **ten** handlers: the
seven ops ported from the sidecar, plus `run-ls` (`_run_ls`) and `build-upload-xlsx`
(`_build_upload_xlsx`), the NExtSEEK-only reingest pair added afterwards, and `graph-schema`
(`_graph_schema`), which serves the live graph catalog so the CC agent stops reading a snapshot
baked into its image. `run_op` refuses any
label absent from that table. The sidecar's own table (`_HANDLERS` in
`NessieAI/docker/ns-sidecar/app/ops.py`) matches it handler for handler. The last two rows below
are not dispatched here at all: they are the pre-existing chat endpoints, listed so the whole
surface is in one place.

| Op | Method + URL | Request model | Response model | chat_nextseek call (as in `NessieAI/docker/ns-sidecar/app/ops.py`) |
|----|--------------|---------------|----------------|------------------------------------------------------------|
| **entity** | POST `/assistant/entity/` | `EntityOpRequest{query}` | `EntityOpResponse` | `entity_agent(config, query)` → `EntityAgentOutput` |
| **parse** | POST `/assistant/parse/` | `ParseOpRequest{query}` | `ParseOpResponse` | `parser_agent(session, config, query, entity_agent(config, query))` → `ParserPlan` |
| **graph** | POST `/assistant/graph/` | `GraphOpRequest{query}` | `GraphOpResponse` | `graph_agent(config, query, entity_agent(config, query))` → `GraphAgentPlan`, **then** `tool_neo4j_query(config, plan.cypher, plan.parameters)`. Result = `{plan, result}`. **Note:** a superset of the sidecar's original op, which returned the plan only |
| **graph-schema** | POST `/assistant/graph-schema/` | `GraphSchemaOpRequest{types?, query?}` | `GraphSchemaOpResponse` | no agent and **no LLM**: `graph_schema_snapshot(config, types=[...], question=query)` reads the live v1.1/1.2 catalog through `graph_catalog` and renders it with `graph_context`. Result = `{source, schema_version, catalog_hash, synced_at, sample_types, resolved_types, unknown_types, schema, vocabulary, unavailable_reason, fallback_fetched_at}`. `source` is `catalog` or `fallback` (the committed `NessieAI/chat_nextseek/src/chat_nextseek/context/neo4j_schema.json`, with the reason). Read-only: the caller sends no Cypher. |
| **api-read** | POST `/assistant/api-read/` | `ApiReadRequest{parser_plan}` | `ApiReadResponse` | `api_agent_build_request(config, json.loads(parser_plan))` → gate `(endpoint, METHOD)` against `read_safe_endpoints.json` → `tool_nextseek_api_request(config, endpoint, method, requestBody, queryParameters)`. Result = `{endpoint, method, api_plan, response}` |
| **api-write** | POST `/assistant/api-write/` | `ApiWriteRequest{parser_plan, confirmed_write=false, query?}` | `ApiWriteResponse` | gate: **executes only when `confirmed_write is True`** (strict bool) else `WRITE_BLOCKED`; then `api_agent_build_request` → `tool_nextseek_api_request`. Result = `{endpoint, method, api_plan, response}` |
| **report** | POST `/assistant/report/` | `ReportOpRequest{mode, project}` | `ReportOpResponse` | `run_reporter_summary(config, ReporterPlan(project, reporter_mode="summary", summary_mode=("RPPR" if mode=="rppr" else mode)), log_dir)` → `(result, saved_files, summary)`. Result = `{summary, saved_files, rows}`. **No LLM** (SQL/Neo4j). |
| **generate-submission** | POST `/assistant/generate-submission/` | `SubmissionRequest{type, uids, query?}` | `SubmissionResponse` | `report_writer_agent(config, query or "Generate a <type> submission report…", ReportWriterPlan(report_type=type, reporter_context={"uids": [...]}))` → `ReportWriterOutput`. **Note:** the query defaults to a non-empty string (a blank user message is rejected by Bedrock/Opus). |
| **run-ls** | POST `/assistant/run-ls/` | `RunLsRequest{run_dir}` | none declared; the `run_ls` action pins only the error envelope | no agent and **no LLM**: `_run_ls` validates that `run_dir` is at or under `<LURIA working_path>/runs`, then calls `ssh_run` with the path shell-quoted. Result = `{run_dir, truncated, tree}`. Never writes to Luria. |
| **build-upload-xlsx** | POST `/assistant/build-upload-xlsx/` | `BuildUploadXlsxRequest{rows, existing_parent_uids, session_id?}` | none declared; the `build_upload_xlsx` action pins only the error envelope | no agent and **no LLM**: `_build_upload_xlsx` runs `qa_rows` per sample type, skips a HARD_REJECT type while still returning its report, then runs `render_upload_workbook` per surviving type. Result = `{saved_files, qa}`. **No NExtSEEK write.** |
| **query** | POST `/assistant/query/` (SSE) | `QueryRequest{query, mode, session_id?, force_new?, use_prod?}` | (SSE stream) | **already exposed**: `run_query(...)`. No change. |
| **plan** | POST `/assistant/query/` with `mode="plan"` (or `/query/async/`) | `QueryRequest{query, mode:"plan", …}` | (SSE / task) | **already exposed**: `run_query_plan(...)` via the `mode` switch. No separate endpoint needed. |

### Arg-schema parity (request body fields)

| Op | Body fields (sidecar `_ws_contract`) | Native request model adds |
|----|-----------------------------------|----------------------------|
| entity / parse / graph | `query` | `use_prod?`, `session_id?` (optional, default-safe) |
| graph-schema | *(not in the sidecar's original set)* `types?` (comma-sep sample type codes), `query?` | `use_prod?` |
| api-read | `parser_plan` (JSON string) | `use_prod?` |
| api-write | `parser_plan`, `confirmed_write` (strict bool), `query?` | `use_prod?` |
| report | `mode` ∈ {samples,protocols,published,rppr}, `project` | `use_prod?` |
| generate-submission | `type` ∈ {GEO,SRA,NFCORE_RNASEQ,NFCORE_SCRNASEQ,PRIDE}, `uids` (comma-sep), `query?` | `use_prod?` |
| run-ls | `run_dir` | `use_prod?` |
| build-upload-xlsx | `rows` (JSON string), `existing_parent_uids` (comma-sep, defaults `""`) | `use_prod?`, `session_id?` |

A caller can ignore the `use_prod`/`session_id` additions; they default safely. Both reingest
models (`RunLsRequest`, `BuildUploadXlsxRequest`) set `extra="forbid"`, so an unknown body field is
a 422, not a silent drop.

## Downloading report / generate-submission / build-upload-xlsx outputs (the HTTP delivery path)

`report`, `generate-submission` and `build-upload-xlsx` produce artifacts that live on NExtSEEK's
filesystem, useless to a remote caller by path alone. So those three ops **register a lightweight
bundle** in the caller's chat session and return a `download` block alongside `result`.
`_run_granular_op` in `nextseek_api/services/assistant.py` gives the three a fresh writable
run-root and calls `_register_artifact_bundle`, which builds the block:

```json
{
  "op": "report",
  "result": { "summary": {...}, "saved_files": {"published_report": "/app/outputs/granular/<id>/..."}, "rows": {...} },
  "download": {
    "session_id": "<uuid>",
    "bundle_id": 1,
    "artifacts": [ { "key": "published_report", "url": "/nextseek_api/assistant/sessions/<uuid>/bundles/1/artifacts/published_report/" } ]
  }
}
```

The caller fetches each artifact with an authenticated `GET` on `download.artifacts[].url` (the
ownership-checked `download_artifact` endpoint). For `generate-submission` (no on-disk file: the
structured output is in `result`), the bundle carries `report_writer_output`, and
`download.artifacts` includes an `all_tables` URL that serves the submission as a combined `.xlsx`.
`report` and `build-upload-xlsx` take the other branch: `saved_files` is served directly and
`report_writer_output` is left empty, the only difference between them being the bundle's `mode`,
`"reingest"` for `build-upload-xlsx` and `"reporter"` for `report`. Pass an optional `session_id`
in the request to attach the bundle to an existing session of the caller's; otherwise a new one is
created. Response models: `DownloadRef` / `ArtifactRef`. The `download` field is declared on
`ReportOpResponse` and `SubmissionResponse` only, so a `build-upload-xlsx` caller reads it off the
raw envelope.

## Write safety (preserved exactly)

`api-write` is **confirmation-only**: `confirmed_write` must be the boolean `True`. The string
`"true"` / integer `1` are rejected at request validation (strict bool) **and** by the server-side
gate (`is True`). The gate fires **before** any agent/LLM call or DB write, so an unconfirmed write
cannot reach the database. `api-read` is allowlist-gated against
`NessieAI/ns/read_safe_endpoints.json`. Source: `build_gate` in `NessieAI/ns/write_gate.py`.

Only two handlers call the gate at all: the `api-read` and `api-write` handlers in
`NessieAI/ns/granular.py`. The other eight, `run-ls` and `build-upload-xlsx` among them, never
reach it, which is why the gate's own `SIDECAR_OPS` frozenset holds the **eight** labels it has a
policy for (the seven ported ones plus `graph-schema`) while the dispatcher holds ten. That set is not a second op catalog: it is the gate's
known-label list, and anything outside it is default-denied. A handler added later that *does*
call the gate with its own label is refused with `WRITE_BLOCKED` until the label is added there.

## Artifact serving (output-type coverage)

`GET /assistant/sessions/{sid}/bundles/{bid}/artifacts/{key}/` serves **every**
`report_saved_files` key as its real on-disk file with the correct Content-Type:
`merged_report` (json), `geo_seq_workbooks` (xlsx), `sra_submission_workbooks`
(xlsx), `sra_biosample_workbooks` (xlsx), `nfcore_*` samplesheets (csv/tsv),
`pride_submission_px` (txt), `pride_sdrf` (tsv), plus reporter_result/metadata/
protocols. **Path-traversal-hardened** (`_safe_artifact_path` in `NessieAI/ns/artifacts.py`): real `relative_to`
containment (no string-prefix bypass) against a narrow root, `<BASE_DIR>/outputs`
plus `NEXTSEEK_OUTPUTS_DIR` only, **not** `BASE_DIR`/home, with `Path.resolve`
canonicalizing symlinks. (The `sample_counts_by_type` chart type from the original spec was
dropped: no chart generator exists anywhere in the pipeline.)

## Downloading a whole session (one zip)

`GET /assistant/sessions/{sid}/download/` streams everything one chat produced as a single
`application/zip`, for the owner (a superuser may download anyone's). It is the delivery path for a
caller who wants every turn rather than one `{bid}/artifacts/{key}` at a time. Built by
`nextseek_api/assistant/session_export.py`:

| Member | Holds |
|---|---|
| `transcript.md`, `transcript.json` | the turns exactly as the chat UI lists them (`turn_rows`, the walk `?include=turns` uses), each with the folder its files are in |
| `turn-NN/` | the files of the NN-th turn on screen: the bundle's `files` manifest and payload pointer (the UI's hidden `graph` and `memory` kinds excluded), every path of every `report_saved_files` key, `report_<bid>.xlsx` when the bundle has report tables, and a Container-CC turn's published files |
| `bundle-N/` | the files of a bundle no turn points at, which is how granular-op bundles (above) arrive |
| `manifest.json`, always last | every member with its size, and every file left out with a reason code: `outside_artifact_root`, `missing_on_disk`, `cc_tree_unresolved`, `generation_failed`, `read_failed`. It never carries a server path |

The two artifact roots keep their own guards. NS files go through `_safe_artifact_path` (above). A
Container-CC turn's files are read from `<CC tree>/output/artifacts/<cc_run_id>/`, where the CC tree
is the project folder the session's CC turns ran in (saved by the turn as
`extra_state['cc_project_dirname']`) under the owner's username, and each file passes
`resolve_artifact_path`; links are never followed, and the engine's own `artifacts.zip` is left out
when the turn's files sit beside it. No SEEK call is made, so a project renamed since the turn ran
does not hide its files, and a superuser downloading another user's chat gets the owner's CC files
too. A session that saved no folder, or a saved name that is not one plain path segment, costs only
the CC files (`cc_tree_unresolved`), never the download.

Every path is checked before the first byte is sent. The zip is then written piece by piece with data
descriptors, as an asynchronous iterator under ASGI and a synchronous one under WSGI, because Django
reads an iterator of the other kind into one list before sending anything.

## Tests

- Free, no LLM: `NessieAI/tests/ns/` for the ops (the `test_granular_*.py` modules,
  `test_run_ls_op.py`, `test_build_upload_xlsx_op.py`) and `NessieAI/tests/api/` for the HTTP
  surface. Both run under the in-memory SQLite `dmac.test_settings`.
- Paid real-stack acceptance: `NessieAI/tests/ns/test_granular_realstack.py`, skipped unless
  `RUN_REALSTACK=1` and held under a hard spend cap. It needs a running stack, a SEEK login valid
  on it and real provider keys, and uses the MySQL-backed `dmac.test_settings_realstack`. The
  recorded runs are frozen in `NessieAI/history/ns/acceptance_evidence/`.

The commands for both, and the MySQL grant the paid lane needs, are in `NessieAI/tests/README.md`.
