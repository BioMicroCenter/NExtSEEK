# Native Assistant Granular-Ops Contract

This is the hand-off contract for rewiring `dmac_assistant`'s nextseek sidecar to
call **native NExtSEEK assistant endpoints** instead of running its own vendored
`chat_nextseek`. After dmac points its ops at these endpoints (and copies the
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

The dispatcher is `_HANDLERS` at `nextseek_api/assistant/granular.py:261-271`, and
it holds **nine** handlers: the seven ops ported from the sidecar, plus `run-ls`
(`nextseek_api/assistant/granular.py:192-211`) and `build-upload-xlsx`
(`nextseek_api/assistant/granular.py:214-259`), the NExtSEEK-only reingest pair
added afterwards. `run_op` refuses any label absent from that table
(`nextseek_api/assistant/granular.py:55-57`). The sidecar's own table matches it
handler for handler (`docker/ns-sidecar/app/ops.py:184-190`). The last two rows
below are not dispatched here at all — they are the pre-existing chat endpoints,
listed so the whole surface is in one place.

| Op | Method + URL | Request model | Response model | chat_nextseek call (verified against `docker/ns-sidecar/app/ops.py`) |
|----|--------------|---------------|----------------|------------------------------------------------------------|
| **entity** | POST `/assistant/entity/` | `EntityOpRequest{query}` | `EntityOpResponse` | `entity_agent(config, query)` → `EntityAgentOutput` |
| **parse** | POST `/assistant/parse/` | `ParseOpRequest{query}` | `ParseOpResponse` | `parser_agent(session, config, query, entity_agent(config, query))` → `ParserPlan` |
| **graph** | POST `/assistant/graph/` | `GraphOpRequest{query}` | `GraphOpResponse` | `graph_agent(config, query, entity_agent(config, query))` → `GraphAgentPlan`, **then** `tool_neo4j_query(config, plan.cypher, plan.parameters)`. Result = `{plan, result}` ⚠️ **superset of dmac** (dmac returns the plan only) |
| **api-read** | POST `/assistant/api-read/` | `ApiReadRequest{parser_plan}` | `ApiReadResponse` | `api_agent_build_request(config, json.loads(parser_plan))` → gate `(endpoint, METHOD)` against `read_safe_endpoints.json` → `tool_nextseek_api_request(config, endpoint, method, requestBody, queryParameters)`. Result = `{endpoint, method, api_plan, response}` |
| **api-write** | POST `/assistant/api-write/` | `ApiWriteRequest{parser_plan, confirmed_write=false, query?}` | `ApiWriteResponse` | gate: **executes only when `confirmed_write is True`** (strict bool) else `WRITE_BLOCKED`; then `api_agent_build_request` → `tool_nextseek_api_request`. Result = `{endpoint, method, api_plan, response}` |
| **report** | POST `/assistant/report/` | `ReportOpRequest{mode, project}` | `ReportOpResponse` | `run_reporter_summary(config, ReporterPlan(project, reporter_mode="summary", summary_mode=("RPPR" if mode=="rppr" else mode)), log_dir)` → `(result, saved_files, summary)`. Result = `{summary, saved_files, rows}`. **No LLM** (SQL/Neo4j). |
| **generate-submission** | POST `/assistant/generate-submission/` | `SubmissionRequest{type, uids, query?}` | `SubmissionResponse` | `report_writer_agent(config, query or "Generate a <type> submission report…", ReportWriterPlan(report_type=type, reporter_context={"uids": [...]}))` → `ReportWriterOutput`. ⚠️ The query defaults to a non-empty string (a blank user message is rejected by Bedrock/Opus). |
| **run-ls** | POST `/assistant/run-ls/` | `RunLsRequest{run_dir}` (`nextseek_api/assistant/models_api.py:302-306`) | none declared; the action pins only the error envelope (`nextseek_api/services/assistant.py:1409-1415`) | no agent and **no LLM** — validates `run_dir` is at or under `<LURIA working_path>/runs` (`nextseek_api/assistant/granular.py:206-207`), then `ssh_run` with the path shell-quoted (`nextseek_api/assistant/granular.py:210`). Result = `{run_dir, truncated, tree}`. Never writes to Luria. |
| **build-upload-xlsx** | POST `/assistant/build-upload-xlsx/` | `BuildUploadXlsxRequest{rows, existing_parent_uids, session_id?}` (`nextseek_api/assistant/models_api.py:309-316`) | none declared; error envelope only (`nextseek_api/services/assistant.py:1420-1426`) | no agent and **no LLM** — `qa_rows` per sample type, a HARD_REJECT type skipped with its report still returned (`nextseek_api/assistant/granular.py:249-250`), then `render_upload_workbook` per surviving type (`nextseek_api/assistant/granular.py:257`). Result = `{saved_files, qa}` (`nextseek_api/assistant/granular.py:259`). **No NExtSEEK write.** |
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
| run-ls | `run_dir` | `use_prod?` |
| build-upload-xlsx | `rows` (JSON string), `existing_parent_uids` (comma-sep, defaults `""`) | `use_prod?`, `session_id?` |

dmac can ignore the `use_prod`/`session_id` additions; they default safely. Both
reingest models set `extra="forbid"`
(`nextseek_api/assistant/models_api.py:306`,
`nextseek_api/assistant/models_api.py:316`), so an unknown body field is a 422,
not a silent drop.

## Downloading report / generate-submission / build-upload-xlsx outputs (the HTTP delivery path)

`report`, `generate-submission` and `build-upload-xlsx` produce artifacts that
live on NExtSEEK's filesystem — useless to a remote dmac caller by path alone. So
those three ops **register a lightweight bundle** in the caller's chat session and
return a `download` block alongside `result`. The three-op membership test is at
`nextseek_api/services/assistant.py:1284-1285`, and the same three get a fresh
writable run-root at `nextseek_api/services/assistant.py:1266`:

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
`report` and `build-upload-xlsx` take the other branch: `saved_files` is served
directly and `report_writer_output` is left empty, the only difference between
them being the bundle's `mode`, `"reingest"` for `build-upload-xlsx` and
`"reporter"` for `report` (`nextseek_api/services/assistant.py:1309-1313`).
Pass an optional `session_id` in the request to attach the bundle to an existing
session; otherwise a new one is created
(`nextseek_api/services/assistant.py:1293-1300`). Response models: `DownloadRef`
(`nextseek_api/assistant/models_api.py:438`) / `ArtifactRef`
(`nextseek_api/assistant/models_api.py:431`); the `download` field is declared on
`ReportOpResponse` (`nextseek_api/assistant/models_api.py:457`) and
`SubmissionResponse` (`nextseek_api/assistant/models_api.py:473`) and nowhere
else — a `/usr/bin/grep` for `download` over
`nextseek_api/assistant/models_api.py` on 2026-09-03 returned no third field — so
a `build-upload-xlsx` caller reads it off the raw envelope.

## Write safety (preserved exactly)

`api-write` is **confirmation-only**: `confirmed_write` must be the boolean
`True`. The string `"true"` / integer `1` are rejected at request validation
(strict bool) **and** by the server-side gate (`is True`). The gate fires
**before** any agent/LLM call or DB write, so an unconfirmed write cannot reach
the database. `api-read` is allowlist-gated against
`nextseek_api/assistant/read_safe_endpoints.json` (a 15-entry JSON array, counted
2026-09-03 by loading the file). Source: `nextseek_api/assistant/write_gate.py:78-100`.

Only two handlers call the gate at all — `nextseek_api/assistant/granular.py:102`
for `api-read` and `nextseek_api/assistant/granular.py:113` for `api-write`; a
`/usr/bin/grep` for `write_gate(` over `nextseek_api/assistant/granular.py` on
2026-09-03 returned those two lines and nothing else. The other seven handlers,
`run-ls` and `build-upload-xlsx` among them, never reach it, which is why the
gate's own `SIDECAR_OPS` frozenset still holds the **seven** ported labels
(`nextseek_api/assistant/write_gate.py:29-31`) while the dispatcher holds nine.
That set is not a second op catalog: it is the gate's known-label list, and
anything outside it is default-denied at
`nextseek_api/assistant/write_gate.py:99-100`. A handler added later that *does*
call the gate with its own label is refused with `WRITE_BLOCKED` until the label
is added there.

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

- Unit/integration (free, no LLM): the `test_granular_*.py` modules plus
  `nextseek_api/assistant/tests/test_run_ls_op.py` and
  `nextseek_api/assistant/tests/test_build_upload_xlsx_op.py` for the two reingest
  ops. Measured on this branch on 2026-09-03 over the whole
  `nextseek_api/assistant/tests` directory: **302 collected — 288 passed, 6
  failed, 8 skipped, 14 subtests passed**, in 11.92s. The 8 skips are the paid
  tests declining themselves; all 6 failures are in
  `nextseek_api/assistant/tests/test_route_capabilities.py`, which tests other
  subsystems' committed state rather than this contract — see
  `nextseek_api/assistant/CLAUDE.md` for why they are not regressions here.
- Real-stack paid acceptance:
  `nextseek_api/assistant/tests/test_granular_realstack.py:101` gates the class
  behind the `RUN_REALSTACK=1` flag read at
  `nextseek_api/assistant/tests/test_granular_realstack.py:33`, under the spend
  cap at `nextseek_api/assistant/tests/test_granular_realstack.py:37`. It needs
  real chat_nextseek + local MySQL/Neo4j/REST + real LLM keys, and was not run for
  this refresh; the committed stand-in is
  `nextseek_api/assistant/tests/acceptance_evidence/`.

**Correction.** An earlier revision of this file said the canonical SQLite test
settings were not viable on this branch. That did not reproduce on 2026-09-03:
`dmac/test_settings.py:21-30` points both database aliases at in-memory SQLite, and
`nextseek_api/assistant/tests/test_granular_endpoints.py` — whose base class is a
real `django.test.TestCase` at
`nextseek_api/assistant/tests/test_granular_endpoints.py:37` — passed 21 of 21
under `DJANGO_SETTINGS_MODULE=dmac.test_settings` with migrations enabled and
`--create-db` forcing a fresh build. The free lane's throwaway-container recipe,
which is the one actually exercised, is in `nextseek_api/assistant/CLAUDE.md`.
The `dmac.test_settings_realstack` module named in the commands below stays
because the paid lane wants the live MySQL/Neo4j
(`dmac/test_settings_realstack.py:3-4`) — but the free suite no longer needs it.
That module's own docstring still repeats the disproven claim
(`dmac/test_settings_realstack.py:5-7`); it is a code comment and is left unfixed
by this refresh.

Both commands run inside the `nextseek` container:

```bash
# free suite (or use the cheaper throwaway-container lane in CLAUDE.md)
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
2. **`dmac/test_settings_realstack.py`** — MySQL-backed test settings, so the paid
   lane runs the normal MySQL-targeted migrations against the live seek-mysql
   (`dmac/test_settings_realstack.py:3-4`). It is not a workaround for the SQLite
   settings, which do build the test database — see the correction above.
3. A scoped, reversible MySQL grant: `GRANT ALL ON \`test_dmac\`.* TO seek_db_user@'%'`
   (lets Django create its test DB). Revoke with the matching `REVOKE` if undesired.
