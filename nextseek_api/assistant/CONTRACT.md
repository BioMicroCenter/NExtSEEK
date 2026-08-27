# Native Assistant Granular-Ops Contract

This is the hand-off contract for rewiring `dmac_assistant`'s nextseek sidecar to
call **native NExtSEEK assistant endpoints** instead of running its own vendored
`chat_nextseek`. After dmac points its 9 ops at these endpoints (and copies the
request/response models below), it can delete the vendored `chat_nextseek` +
torch + sentence-transformers from its sidecar.

All endpoints are **additive** to the existing `AssistantViewSet`
(`nextseek_api/services/assistant.py`); existing endpoint behavior is unchanged.

- **Base path:** `/nextseek_api/assistant/`
- **Auth:** same as the existing assistant endpoints — `TokenAuthentication`,
  CSRF-exempt `SessionAuthentication`, or `BasicAuthentication`; the caller must
  be `IsAuthenticated` **and** in a participating project
  (`UserInParticipatingProject`). For `api-read` / `api-write` the caller's
  NExtSEEK credentials (HTTP Basic, or session `username`/`password`) are
  injected into a per-request `ChatConfig` copy and used for the outbound call —
  exactly as `query`/`query_async` do.
- **Success envelope:** `200` with `{"op": "<op>", "result": { … }}`.
- **Error envelope:** `{"code": "<CODE>", "errors": [{"title": "<CODE>", "detail": "…"}]}`
  with the canonical dmac code so the thin client maps to its CLI exit:
  `VALIDATION` (422), `WRITE_BLOCKED` (403), `AGENT_FAILED` (502),
  `CONFIG_ERROR`/`CONFIG_MISSING` (500). Unauthenticated → `401`; not in a
  participating project → `403`.
- **Models to copy:** `nextseek_api/assistant/models_api.py` — request models
  mirror the dmac `_ws_contract` arg schemas; response models are a typed
  `{op, result}` envelope over a lenient (`extra="allow"`) result.

## Op table

| Op | Method + URL | Request model | Response model | chat_nextseek call (verified against `sidecar/app/ops.py`) |
|----|--------------|---------------|----------------|------------------------------------------------------------|
| **entity** | POST `/assistant/entity/` | `EntityOpRequest{query}` | `EntityOpResponse` | `entity_agent(config, query)` → `EntityAgentOutput` |
| **parse** | POST `/assistant/parse/` | `ParseOpRequest{query}` | `ParseOpResponse` | `parser_agent(session, config, query, entity_agent(config, query))` → `ParserPlan` |
| **graph** | POST `/assistant/graph/` | `GraphOpRequest{query}` | `GraphOpResponse` | `graph_agent(config, query, entity_agent(config, query))` → `GraphAgentPlan`, **then** `tool_neo4j_query(config, plan.cypher, plan.parameters)`. Result = `{plan, result}` ⚠️ **superset of dmac** (dmac returns the plan only) |
| **api-read** | POST `/assistant/api-read/` | `ApiReadRequest{parser_plan}` | `ApiReadResponse` | `api_agent_build_request(config, json.loads(parser_plan))` → gate `(endpoint, METHOD)` against `read_safe_endpoints.json` → `tool_nextseek_api_request(config, endpoint, method, requestBody, queryParameters)`. Result = `{endpoint, method, api_plan, response}` |
| **api-write** | POST `/assistant/api-write/` | `ApiWriteRequest{parser_plan, confirmed_write=false, query?}` | `ApiWriteResponse` | gate: **executes only when `confirmed_write is True`** (strict bool) else `WRITE_BLOCKED`; then `api_agent_build_request` → `tool_nextseek_api_request`. Result = `{endpoint, method, api_plan, response}` |
| **report** | POST `/assistant/report/` | `ReportOpRequest{mode, project}` | `ReportOpResponse` | `run_reporter_summary(config, ReporterPlan(project, reporter_mode="summary", summary_mode=("RPPR" if mode=="rppr" else mode)), log_dir)` → `(result, saved_files, summary)`. Result = `{summary, saved_files, rows}`. **No LLM** (SQL/Neo4j). |
| **generate-submission** | POST `/assistant/generate-submission/` | `SubmissionRequest{type, uids, query?}` | `SubmissionResponse` | `report_writer_agent(config, query or "Generate a <type> submission report…", ReportWriterPlan(report_type=type, reporter_context={"uids": [...]}))` → `ReportWriterOutput`. ⚠️ The query defaults to a non-empty string (a blank user message is rejected by Bedrock/Opus). |
| **query** | POST `/assistant/query/` (SSE) | `QueryRequest{query, mode, session_id?, force_new?, use_prod?}` | (SSE stream) | **already exposed** — `run_query(...)`. No change. |
| **plan** | POST `/assistant/query/` with `mode="plan"` (or `/query/async/`) | `QueryRequest{query, mode:"plan", …}` | (SSE / task) | **already exposed** — `run_query_plan(...)` via the `mode` switch. No separate endpoint needed. |

### Arg-schema parity (request body fields)

| Op | Body fields (dmac `_ws_contract`) | Native request model adds |
|----|-----------------------------------|----------------------------|
| entity / parse / graph | `query` | `use_prod?`, `session_id?` (optional, default-safe) |
| api-read | `parser_plan` (JSON string) | `use_prod?` |
| api-write | `parser_plan`, `confirmed_write` (strict bool), `query?` | `use_prod?` |
| report | `mode` ∈ {samples,protocols,published,rppr}, `project` | `use_prod?` |
| generate-submission | `type` ∈ {GEO,SRA,NFCORE_RNASEQ,NFCORE_SCRNASEQ,PRIDE}, `uids` (comma-sep), `query?` | `use_prod?` |

dmac can ignore the `use_prod`/`session_id` additions; they default safely.

## Downloading report / generate-submission outputs (the HTTP delivery path)

`report` and `generate-submission` produce artifacts that live on NExtSEEK's
filesystem — useless to a remote dmac caller by path alone. So those two ops
**register a lightweight bundle** in the caller's chat session and return a
`download` block alongside `result`:

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

dmac fetches each artifact with an authenticated `GET` on `download.artifacts[].url`
(the ownership-checked `download_artifact` endpoint) and writes the bytes to
Dropbox. For `generate-submission` (no on-disk file — the structured output is in
`result`), the bundle carries `report_writer_output` and the `download.artifacts`
includes an `all_tables` URL that serves the submission as a combined `.xlsx`.
Pass an optional `session_id` in the request to attach the bundle to an existing
session; otherwise a new one is created. Response models: `DownloadRef` /
`ArtifactRef` in `models_api.py`; the `download` field is `Optional` on
`ReportOpResponse` / `SubmissionResponse` only.

## Write safety (preserved exactly)

`api-write` is **confirmation-only**: `confirmed_write` must be the boolean
`True`. The string `"true"` / integer `1` are rejected at request validation
(strict bool) **and** by the server-side gate (`is True`). The gate fires
**before** any agent/LLM call or DB write, so an unconfirmed write cannot reach
the database. `api-read` is allowlist-gated against
`nextseek_api/assistant/read_safe_endpoints.json` (the canonical 15-entry
read-safe list copied from dmac). Source: `nextseek_api/assistant/write_gate.py`.

## Artifact serving (output-type coverage)

`GET /assistant/sessions/{sid}/bundles/{bid}/artifacts/{key}/` now serves **every**
`report_saved_files` key as its real on-disk file with the correct Content-Type:
`merged_report` (json), `geo_seq_workbooks` (xlsx), `sra_submission_workbooks`
(xlsx), `sra_biosample_workbooks` (xlsx), `nfcore_*` samplesheets (csv/tsv),
`pride_submission_px` (txt), `pride_sdrf` (tsv), plus reporter_result/metadata/
protocols. **Path-traversal-hardened** (`_safe_artifact_path`): real `relative_to`
containment (no string-prefix bypass) against a narrow root — `<BASE_DIR>/outputs`
+ `NEXTSEEK_OUTPUTS_DIR` only, **not** `BASE_DIR`/home — with `Path.resolve`
canonicalizing symlinks. (The `sample_counts_by_type`
chart type from the original spec was dropped: no chart generator exists anywhere
in the pipeline — decision recorded in the session report.)

## Tests + run command

- Unit/integration (free, no LLM): `nextseek_api/assistant/tests/test_granular_*.py`
  (write_gate 10, dispatch 14, models 15, endpoints 13, artifacts 9). Full
  assistant suite: **196 tests, 188 active green, no regressions.**
- Real-stack paid acceptance (gated by `RUN_REALSTACK=1`):
  `tests/test_granular_realstack.py` — 8/8 green against real chat_nextseek +
  local MySQL/Neo4j/REST + real LLM, with a strict token-cost ledger.

Run inside the `nextseek` container (the canonical SQLite test settings are not
viable on this branch — a non-nextseek_api migration uses MySQL-only DDL):

```bash
# free suite
docker exec nextseek sh -lc 'cd /app && uv run python manage.py test \
  nextseek_api.assistant.tests --settings=dmac.test_settings_realstack --noinput --keepdb'

# paid real-stack acceptance (needs a local SEEK login for api-read/api-write)
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=<user> -e SEEK_TEST_PASS=<pass> nextseek sh -lc \
  'cd /app && uv run python manage.py test nextseek_api.assistant.tests.test_granular_realstack \
   --settings=dmac.test_settings_realstack --noinput --keepdb'
```

### Test-infra prerequisites added this work (see session report)

1. **`nextseek_api/migrations/0005_chatsession_extra_state_column.py`** — idempotent
   guard migration that creates `assistant_chat_session.extra_state` on fresh DBs
   (the committed chain left it uncreated; the live DB had it via manual ALTER).
   Production-safe no-op where the column already exists.
2. **`dmac/test_settings_realstack.py`** — MySQL-backed test settings (the shipped
   SQLite `test_settings.py` can't build the test DB on this branch).
3. A scoped, reversible MySQL grant: `GRANT ALL ON \`test_dmac\`.* TO seek_db_user@'%'`
   (lets Django create its test DB). Revoke with the matching `REVOKE` if undesired.
