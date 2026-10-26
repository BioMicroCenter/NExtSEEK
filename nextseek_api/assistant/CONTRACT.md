# Native Assistant Granular-Ops Contract

The HTTP contract for the native granular ops: what the Container-CC agent calls to run one piece
of the NS engine, directly on the default road, or through the `ns-sidecar` on the sidecar road
(`NEXTSEEK_CC_OPS_ROAD`). The ops run in `NessieAI/ns/`
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
  A Container-CC turn sends its turn pass instead (`Authorization: NextseekTurn <pass>`,
  `TurnPassAuthentication`; the sidecar relays it on the sidecar road). A pass is accepted only for the rows of the allow table
  (`nextseek_api/assistant/turn_pass_allow.py`), so `api-write` is refused to it
  (`PASS_NOT_ALLOWED`); its downstream calls act as the login Django holds for the
  turn, never the prod config.
- **Success envelope:** `200` with `{"op": "<op>", "result": { … }}`.
- **Error envelope:** `{"code", "reason", "message", "errors"}`, built only in `nextseek_api/assistant/op_errors.py`.
  `code` is closed: `VALIDATION` (422), `WRITE_BLOCKED` (403), `AUTH_FAILED` (401), `PASS_NOT_ALLOWED` (403),
  `BUSY` (429), `TIME_UP` (408; only for an op that calls a model, under a turn pass, with under 20 s usable before the turn's deadline less 45 s; a container may send `X-Nextseek-Deadline` (Unix seconds), which can only bring the turn's deadline forward; a nested query, plan or pipeline turn (`POST /query/async/` under a turn pass) gets the same `TIME_UP` when under 20 s is left before that deadline less 45 s), `AGENT_FAILED` (502). `reason` is set only with `AGENT_FAILED`
  (`model_unavailable`, `deadline`, `bad_output`, `internal`), else `null`. `message` is a fixed sentence per code
  and reason, never an exception's text or a value the caller sent. `errors` is `[{"field", "type"}, ...]` for
  `VALIDATION` (the refused field and what was wrong, never its value) and `[{"title": "<CODE>", "detail":
  "<message>"}]` for any other code (kept for the sidecar, one release). Unauthenticated → `401`; not in a
  participating project → `403`.
- **Models:** `nextseek_api/assistant/models_api.py`. Request models mirror the sidecar's
  `_ws_contract` arg schemas; response models are a typed `{op, result}` envelope over a lenient
  (`extra="allow"`) result.

## Op table

The dispatcher is `_HANDLERS` in `NessieAI/ns/granular.py`, and it holds **fifteen** handlers: the
seven ops ported from the sidecar, plus `run-ls` (`_run_ls`), `build-upload-xlsx`
(`_build_upload_xlsx`), `run-harvest` (`_run_harvest`), `run-checksum` (`_run_checksum`) and
`upload-reingest` (`_upload_reingest`), the NExtSEEK-only reingest ops added afterwards,
`paper-reingest-start` (`_paper_reingest_start`, body in `NessieAI/ns/paper/start.py`), which starts a paper reingest run,
`graph-schema` (`_graph_schema`), which serves the live graph catalog so the CC agent stops reading
a snapshot baked into its image, and `aggregate` (`_aggregate`, body in `NessieAI/ns/aggregate.py`),
which answers counts and breakdowns in one call. `run_op` refuses any
label absent from that table. The sidecar's own table (`_HANDLERS` in
`NessieAI/docker/ns-sidecar/app/ops.py`) matches it handler for handler. The last two rows below
are not dispatched here at all: they are the pre-existing chat endpoints, listed so the whole
surface is in one place.

| Op | Method + URL | Request model | Response model | chat_nextseek call (as in `NessieAI/docker/ns-sidecar/app/ops.py`) |
|----|--------------|---------------|----------------|------------------------------------------------------------|
| **entity** | POST `/assistant/entity/` | `EntityOpRequest{query}` | `EntityOpResponse` | `entity_agent(config, query)` → `EntityAgentOutput` |
| **parse** | POST `/assistant/parse/` | `ParseOpRequest{query}` | `ParseOpResponse` | `parser_agent(session, config, query, entity_agent(config, query))` → `ParserPlan` |
| **graph** | POST `/assistant/graph/` | `GraphOpRequest{query, plan?}` | `GraphOpResponse` | `graph_agent(config, query, entity_agent(config, query), parser_plan)` → `GraphAgentPlan`, **then** `tool_neo4j_query(config, plan.cypher, plan.parameters)`. Result = `{plan, result, parser_plan, plan_source}` (`plan` in the request: optional JSON `{intent_summary, filters{sampletype_code, assay_codes, keywords, uids, lab_codes}, notes}`, `extra=forbid`, at most 20 items of 200 characters per list, 1,000 characters per text; Django sets `mode` and `resolved` (the turn's vocabulary); a lab code outside the vocabulary or any other field, Cypher, project and endpoint included, is VALIDATION naming `plan.<field>` and no model is called. With no plan the turn's one plan (`CCTurn.plans['turn']`, the latest an op ran) is used, else one parser call whose plan becomes the turn's. `plan_source`: `agent`, `turn` or `parser`. `parser_plan`: the plan the answer ran; `fallback` too when graph_search answered). **Note:** a superset of the sidecar's original op, which returned the plan only |
| **aggregate** | POST `/assistant/aggregate/` | `AggregateOpRequest{query, parts?, plan?}` | `AggregateOpResponse` | `run_aggregate`: `parts` is a JSON array of 1 to 4 plain-language sub-questions or `{question, filters}` objects (a part's own `filters` replace the plan's for that part; empty: the question is the one part); `plan` as for graph, shared by all parts, else the turn's, else one parser call on the whole question (`plan_source` on the result); under a turn pass the vocabulary is the turn's (`CCTurn.vocabulary`, resolved once on the user's question), else `entity_agent` runs **once** over the question and the parts, then each part runs the graph op's chain (`run_graph_question` in `granular.py`: `graph_agent` with the aggregate brief as `refine_context`, `tool_neo4j_query` on the statement with `LIMIT 1001` appended when it has none, at most one retry, graph_search on a scope refusal) on a pool of up to 4 threads. Answers 5 s inside the op's time limit (85 s of the default 90 s; 50 s of 55 s on the sidecar road) with what finished; the other parts are `timed_out`. Result = `{question, complete, elapsed_s, deadline_s, parts: [{part, question, status, kind, columns, groups, group_count, sum_of_group_counts, groups_may_overlap, null_group, truncated, cypher, scope, attempts, fallback, error?}], notes}`. `sum_of_group_counts` adds the groups' counts, so a sample in several groups (project, assay, study, a list value) counts once in each: `groups_may_overlap` is true for every breakdown of two or more groups, and then the sum is not a number of samples. No sample records and no Cypher or scope from the caller. |
| **graph-schema** | POST `/assistant/graph-schema/` | `GraphSchemaOpRequest{types?, query?}` | `GraphSchemaOpResponse` | no agent and **no LLM**: `graph_schema_snapshot(config, types=[...], question=query)` reads the live v1.1/1.2 catalog through `graph_catalog` and renders it with `graph_context`. Result = `{source, schema_version, catalog_hash, synced_at, sample_types, resolved_types, unknown_types, schema, vocabulary, unavailable_reason, fallback_fetched_at, assays}`. `source` is `catalog` or `fallback` (the committed `NessieAI/chat_nextseek/src/chat_nextseek/context/neo4j_schema.json`, with the reason). `assays` is this instance's assay titles (`ChatConfig.MIN_ASSAYS`, from its `dmac.internal_assays`), the same for every caller and present on both paths. Read-only: the caller sends no Cypher. |
| **api-read** | POST `/assistant/api-read/` | `ApiReadRequest{parser_plan}` | `ApiReadResponse` | `api_agent_build_request(config, json.loads(parser_plan))` → gate `(endpoint, METHOD)` against `read_safe_endpoints.json` → `tool_nextseek_api_request(config, endpoint, method, requestBody, queryParameters)`. Result = `{endpoint, method, api_plan, response}` |
| **api-write** | POST `/assistant/api-write/` | `ApiWriteRequest{parser_plan, confirmed_write=false, query?}` | `ApiWriteResponse` | gate: **executes only when `confirmed_write is True`** (strict bool) else `WRITE_BLOCKED`; then `api_agent_build_request` → `tool_nextseek_api_request`. Result = `{endpoint, method, api_plan, response}` |
| **report** | POST `/assistant/report/` | `ReportOpRequest{mode, project}` | `ReportOpResponse` | `run_reporter_summary(config, ReporterPlan(project, reporter_mode="summary", summary_mode=("RPPR" if mode=="rppr" else mode)), log_dir)` → `(result, saved_files, summary)`. Result = `{summary, saved_files, rows}`. **No LLM** (SQL/Neo4j). |
| **generate-submission** | POST `/assistant/generate-submission/` | `SubmissionRequest{type, uids, query?}` | `SubmissionResponse` | `report_writer_agent(config, query or "Generate a <type> submission report…", ReportWriterPlan(report_type=type, reporter_context={"uids": [...]}))` → `ReportWriterOutput`. **Note:** the query defaults to a non-empty string (a blank user message is rejected by Bedrock/Opus). |
| **run-ls** | POST `/assistant/run-ls/` | `RunLsRequest{run_dir}` | none declared; the `run_ls` action pins only the error envelope | no agent and **no LLM**: `_run_ls` validates that `run_dir` is at or under `<LURIA working_path>/runs`, then calls `ssh_run` with the path shell-quoted. Result = `{run_dir, truncated, tree}`. Never writes to Luria. |
| **build-upload-xlsx** | POST `/assistant/build-upload-xlsx/` | `BuildUploadXlsxRequest{manifest_id?, mode?, answers?, rows?, existing_parent_uids?, session_id?}` | none declared; the `build_upload_xlsx` action pins only the error envelope | no agent and **no LLM**. Two calling conventions, exactly one per call: `manifest_id` + `mode` (`new` or `update`) [+ `answers`], where the server loads its own copy of the harvested manifest and derives every row; or the legacy `rows` [+ `existing_parent_uids`], which takes no `answers`. Either way `_build_upload_xlsx` runs `qa_rows` per sample type, skips a HARD_REJECT type while still returning its report, then runs `render_upload_workbook` per surviving type. The manifest path returns `{saved_files, qa, reply, proposals, answers_deferred, builds}`: `builds` is one build record per workbook (sha256 build id, disposition, project) and `answers_deferred` lists answers held for the other mode's call. The legacy rows path returns `{saved_files, qa}`. **No NExtSEEK write.** |
| **upload-reingest** | POST `/assistant/upload-reingest/` | `UploadReingestRequest{build_ids, confirmed_write (strict bool), use_prod (ignored)}` | none declared | no agent and **no LLM**: `_upload_reingest` calls the gate as `api-write` first, then `upload.run` verifies each build record of the caller, refuses any build not shown in an earlier, completed turn of the chat this call runs in (both looked up by the server, never sent by the caller; the call must come from the caller's one running Container-CC turn), stages and re-hashes every workbook, and starts one batch-upload job per workbook through `dispatch_batch_job` with the caller's project scope (a project outside it is refused), new mode first. Result = `{jobs, reply}`; each job is `{build_id, artifact_key, sample_type, mode, job_id}` or carries `error` instead of `job_id`. **Writes to NExtSEEK; files only, never rows.** |
| **paper-reingest-start** | POST `/assistant/paper-reingest-start/` | `PaperReingestStartRequest{paper, project?, use_prod (ignored)}` | none declared; the action pins the 401 and 422 error envelopes | no agent and **no LLM**: `_paper_reingest_start` hands `NessieAI.ns.paper.start.start` the seams `nextseek_api.paper_reingest.host.start_session` builds. It refuses a caller who is not a superuser (`superusers_only`) and an instance where `NEXTSEEK_PAPER_REINGEST` is not `1` (`feature_off`), then runs the launch check (Europe PMC, or an attached PDF read from the caller's own input folder; the paper's DOI is looked up against `samples` in SEEK's database to decide `new` or `update` mode), and only when the check passes creates a `PaperReingestRun` and queues stage 1 on the `paper_reingest` Celery queue. Result = `{run_id, status, reply}` plus `mode` and `paper{pmid, pmcid, doi, title}` when `status` is `ok`, `missing` (a list of section names) on the statuses that queue nothing, and `state` when `already_running`. `status` is `ok`, `needs_pdf`, `missing_sections`, `not_found` or `already_running`; all but `ok` and `already_running` have `run_id` null and queue nothing. A refusal is VALIDATION (422) on field `paper` with `error_type` `superusers_only`, `feature_off`, `unrecognised_paper`, `source_unavailable`, `source_not_allowed`, `source_bad_answer` (Europe PMC answered with a 4xx, a body that is not JSON, or JATS that does not parse), `pdf_too_large`, `pdf_unreadable`, `catalog_unavailable`, `out_of_time` or `queue_unavailable` (the run row was created but stage 1 could not be queued; the row is left `failed` at stage `queue`). **Never calls the write gate and never writes to SEEK or the graph; its only rows are the run's own.** |
| **run-harvest** | POST `/assistant/run-harvest/` | `RunHarvestRequest{run_dir, allow_failed_run?}` | none declared; the `run_harvest` action pins only the error envelope | no agent and **no LLM**: `_run_harvest` validates `run_dir` exactly as `_run_ls` does (shared `_validate_run_dir`), stages every `harvest.GENERIC_GLOBS` match off Luria into a temp dir, and calls `harvest.harvest_local` on it. Refuses (VALIDATION) a run with a failed process unless `allow_failed_run` is set. Result = `{run_dir, manifest_id, manifest}`. Never writes to Luria or to NExtSEEK. |
| **run-checksum** | POST `/assistant/run-checksum/` | `RunChecksumRequest{run_dir, paths, manifest_id?}` | none declared; the `run_checksum` action pins only the error envelope | no agent and **no LLM**: `_run_checksum` validates `run_dir` exactly as `_run_ls` does (shared `_validate_run_dir`), then runs a remote Python script over SSH that md5s each caller-named relative path under `run_dir`. Unlike `run-harvest`'s glob matches, `paths` is caller-supplied, so a symlink, hardlink, traversal, or any other resolve-outside-`run_dir` escape is a hard VALIDATION error naming every offending path, never a silent skip; only a genuinely missing/non-regular-file path is reported in `skipped`. With `manifest_id`, also loads that run-harvest manifest, merges the new checksums into `RunManifest.checksums`, and re-saves it — manifests are content-addressed (`store.save_manifest`), so this produces a NEW id rather than mutating the one loaded. Refuses (VALIDATION) when the loaded manifest's own `run_dir` does not match the `run_dir` being hashed, so a stale `manifest_id` from a different run can never fold its checksums into this run's outputs. Result = `{run_dir, checksums, skipped, manifest_id?}` (the `manifest_id` key is present, and is the NEW id, only when one was supplied). Never writes to Luria or to NExtSEEK. Separate op from `run-harvest` because hashing multi-GB BAMs during harvest would blow the harvest step's wall clock; which file becomes `File_PrimaryData` is derived server-side from the committed map (`NessieAI/ns/reingest/mapper.py`) once `build-upload-xlsx` runs, independent of when (or whether) it was checksummed. |
| **query** | POST `/assistant/query/` (SSE) | `QueryRequest{query, mode, session_id?, force_new?, use_prod?}` | (SSE stream) | **already exposed**: `run_query(...)`. No change. |
| **plan** | POST `/assistant/query/` with `mode="plan"` (or `/query/async/`) | `QueryRequest{query, mode:"plan", …}` | (SSE / task) | **already exposed**: `run_query_plan(...)` via the `mode` switch. No separate endpoint needed. |

### Arg-schema parity (request body fields)

| Op | Body fields (sidecar `_ws_contract`) | Native request model adds |
|----|-----------------------------------|----------------------------|
| entity / parse / graph | `query` (graph also `plan?`, JSON text) | `use_prod?`, `session_id?` (optional, default-safe) |
| graph-schema | *(not in the sidecar's original set)* `types?` (comma-sep sample type codes), `query?` | `use_prod?` |
| aggregate | *(not in the sidecar's original set)* `query`, `parts?` (JSON array of 1 to 4 strings or objects, as text), `plan?` (JSON text) | `use_prod?`, `session_id?` |
| api-read | `parser_plan` (JSON string) | `use_prod?` |
| api-write | `parser_plan`, `confirmed_write` (strict bool), `query?` | `use_prod?` |
| report | `mode` ∈ {samples,protocols,published,rppr}, `project` | `use_prod?` |
| generate-submission | `type` ∈ {GEO,SRA,NFCORE_RNASEQ,NFCORE_SCRNASEQ,PRIDE}, `uids` (comma-sep), `query?` | `use_prod?` |
| run-ls | `run_dir` | `use_prod?` |
| build-upload-xlsx | `rows` (JSON string), `existing_parent_uids` (comma-sep, defaults `""`) | `use_prod?`, `session_id?`, `answers?` |
| upload-reingest | `build_ids` (comma-sep), `confirmed_write` (strict bool) | `use_prod?` (ignored) |
| paper-reingest-start | *(not in the sidecar's original set)* `paper` (1 to 1024 characters: a PMID, PMCID, DOI, article link or the name of an attached PDF), `project?` (SEEK project id, int) | `use_prod?` (ignored) |
| run-harvest | `run_dir`, `allow_failed_run` (bool, defaults `false`) | `use_prod?` |
| run-checksum | `run_dir`, `paths` (comma-sep relative paths), `manifest_id?` | `use_prod?` |

A caller can ignore the `use_prod`/`session_id` additions; they default safely. All the reingest
models (`RunLsRequest`, `BuildUploadXlsxRequest`, `RunHarvestRequest`, `RunChecksumRequest`, `UploadReingestRequest`, `PaperReingestStartRequest`) set
`extra="forbid"`, so an unknown body field is a 422, not a silent drop.

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
`"reingest"` for `build-upload-xlsx` and `"reporter"` for `report`. Under a turn pass the bundle
always lands in the pass's own chat: a `session_id`, if sent, must name that chat (any other is
`PASS_NOT_ALLOWED`, 403). Otherwise an optional `session_id` in the request attaches the bundle to
an existing session of the caller's, and without one a new session is created. Bundle ids are
allocated per chat under a row lock. Response models: `DownloadRef` / `ArtifactRef`. The `download`
field is declared on `ReportOpResponse` and `SubmissionResponse` only, so a `build-upload-xlsx`
caller reads it off the raw envelope.

## Write safety (preserved exactly)

`api-write` is **confirmation-only**: `confirmed_write` must be the boolean `True`. The string
`"true"` / integer `1` are rejected at request validation (strict bool) **and** by the server-side
gate (`is True`). The gate fires **before** any agent/LLM call or DB write, so an unconfirmed write
cannot reach the database. `api-read` is allowlist-gated against
`NessieAI/ns/read_safe_endpoints.json`. Source: `build_gate` in `NessieAI/ns/write_gate.py`.

Only two labels ever reach the gate: `api-read` and `api-write`. The `api-read` and `api-write`
handlers in `NessieAI/ns/granular.py` call it, and so does `upload-reingest`, which reaches it under
the `api-write` label and so needs the same strict `True`; so does the graph_search fallback of
`graph` and `aggregate`, which gates its one request as an `api-read`. No handler calls it with its
own label; the other reingest ops, `run-ls`, `build-upload-xlsx`, `run-harvest`, `run-checksum` and
`paper-reingest-start`, never reach it at all (`paper-reingest-start` has its own superuser and
`NEXTSEEK_PAPER_REINGEST` checks), which is why the gate's own `SIDECAR_OPS` frozenset holds the **nine** labels
it has a policy for (the seven ported ones plus `graph-schema` and `aggregate`) while the dispatcher
holds fifteen. That set is not a second op catalog: it is the gate's known-label list, and anything
outside it is default-denied. A handler added later that *does* call the gate with its own label is
refused with `WRITE_BLOCKED` until the label is added there.

`run-harvest` is read-only for the same reason `run-ls` is: it only stages files off Luria (SSH)
and parses them locally, and never calls `write_gate`, `_run_granular_op`'s own gate check, or
NExtSEEK's write API. It is intentionally absent from `write_gate.SIDECAR_OPS` — adding it there
would not make it safer, since nothing in its handler ever calls the gate to begin with, and it
would make the label-set mismatch above harder to explain to the next reader.

`run-checksum` is read-only for the same reason: it only SSHes Luria to md5 files, returns the
digests, and — when `manifest_id` is given — folds them into a NEW, content-addressed manifest
(never mutating the one behind the original id) and returns that manifest's id too. It never calls
`write_gate` or NExtSEEK's write API, and is likewise absent from `write_gate.SIDECAR_OPS`. Because
`paths` is caller-supplied rather than a glob match, its
containment/symlink/hardlink checks raise `OpValidationError` directly (VALIDATION) instead of
degrading to a silent "skipped" entry — the safety response to an out-of-bounds request is a hard
refusal, not a softer omission, precisely because nothing here ever reaches `write_gate` to catch
it a second way.

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
  `test_run_ls_op.py`, `test_build_upload_xlsx_op.py`, `reingest/test_run_harvest_op.py`,
  `reingest/test_run_checksum_op.py`) and `NessieAI/tests/api/` for the HTTP surface. Both run
  under the in-memory SQLite `dmac.test_settings`.
- Paid real-stack acceptance: `NessieAI/tests/ns/test_granular_realstack.py`, skipped unless
  `RUN_REALSTACK=1` and held under a hard spend cap. It needs a running stack, a SEEK login valid
  on it and real provider keys, and uses the MySQL-backed `dmac.test_settings_realstack`. The
  recorded runs are frozen in `NessieAI/history/ns/acceptance_evidence/`.

The commands for both, and the MySQL grant the paid lane needs, are in `NessieAI/tests/README.md`.
