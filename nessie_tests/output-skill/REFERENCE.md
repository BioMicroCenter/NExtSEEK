# Reference: dev box, data sources, field mapping

Everything needed to triage a nessie_tests run. Read the **field alias table**
before interpreting any criterion; it is the single most common source of wrong
conclusions.

---

## 1. Dev box access

```bash
ssh fairdata-dev sudo -n -u service-account <cmd>
```

Works unattended from the maintainer's host. Always append `< /dev/null` to the
ssh invocation so it cannot block on stdin.

| Thing | Value |
|---|---|
| Repo | `/home/service-account/Documents/Programs/NExtSEEK` (origin `BMCBCC/NExtSEEK`, redirects to `BioMicroCenter`) |
| App container | `nextseek` (Django, gunicorn on `0.0.0.0:8000` in-container) |
| DB container | `seek-mysql`, schema `dmac` |
| CC image | `dmac-assistant:poc`, ephemeral container per CC turn |
| Others | `nextseek-sidecar`, `nextseek-nextseek_nginx-1`, `dmac-bedrock-proxy`, `neo4j`, `seek`, `seek-workers`, `seek-solr` |

**Quoting.** Complex remote commands fight ssh + sudo + `bash -c`. Write the
script locally, base64 it, and run `echo <b64> | base64 -d | bash`. `fetch_run.py`
does this. Do **not** pipe command *output* through `gzip | base64`; that reads as
an opaque blob and gets blocked. Emit plain text or JSON.

**Encoding.** Query text in the corpus contains non-UTF8 bytes (a latin-1 em dash).
Always decode with `errors="replace"`.

### Running the harness

```bash
docker exec -w /app nextseek uv run manage.py nessie \
    --tier {route,full} --scope {specific,all} --sample <ratio> --out /app/nessie_out_X
```

`route` tier is cheaper and needs no seed data. `full` executes real turns, costs
money, and needs the seeded v2 instance (project ids 2-14).

**`route` is not free, and its printed cost is `unmeasured`, not `$0`.** It stops
the *client* polling at `route_decided`; the server finishes and bills for every
gate anyway. Read the Cadence section of `nessie_tests/README.md` before drawing
any conclusion about what a run spent — a `$…` figure in a triage report is a
floor, never a total.

Deploy a harness change with no rebuild, since it is test tooling and not the
served app:

```bash
git pull
docker cp nessie_tests nextseek:/app/
docker cp nextseek_api/management/commands/nessie.py nextseek:/app/nextseek_api/management/commands/nessie.py
```

App, backend or UI changes **do** need `./startup.sh rebuild`, plus
`collectstatic` if anything under `static/` changed, plus
`docker compose build cc-agent` for CC-side changes. Check `df -h` first; the box
has run out of disk mid-checkout before.

Unit suite:

```bash
docker exec -w /app -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
    uv run pytest nessie_tests/tests/ nessie_tests/tests_container/ --no-migrations -q
```

### Watching a run in progress

One `assistant_query_task` row per turn is the real meter. Counting `outputs/`
directories is misleading, because the async endpoint does not create one per turn.

```sql
SELECT status, COUNT(*) FROM assistant_query_task
WHERE created_at > NOW() - INTERVAL 15 MINUTE GROUP BY status;
```

---

## 2. The database: where observed values live

Table `dmac.assistant_query_task`:

| Column | Contents |
|---|---|
| `id` | referred to as the "task id" throughout |
| `query` | the user query text; join key back to a catalog variant |
| `status` | `completed` / `error` / `running` |
| `progress` | JSON array of events. `[0]` is `route_decided`; the last `query_complete` carries `data.debug` |
| `result` | final `{reply, bundle_id, cc_session_id, total_cost_usd, ...}` |
| `created_at` / `updated_at` | real server-side duration, independent of what the harness observed |

The password is read from the container, never hard-coded:

```bash
PW=$(docker exec seek-mysql sh -c 'echo $MYSQL_ROOT_PASSWORD')
```

### Reaching the debug object

`progress` is TEXT holding JSON, and MySQL casts implicitly inside JSON functions.
The debug object sits on the `query_complete` event:

```sql
SET @D = "JSON_EXTRACT(JSON_EXTRACT(progress,'$[*].data.debug'),";
-- a single field:
SELECT JSON_EXTRACT(JSON_EXTRACT(progress,'$[*].data.debug'),'$[0].parser_plan.mode')
FROM assistant_query_task WHERE id = 755;
-- what keys exist at all:
SELECT JSON_KEYS(JSON_EXTRACT(JSON_EXTRACT(progress,'$[*].data.debug'),'$[0]'))
FROM assistant_query_task WHERE id = 755;
```

Keys present in the live debug object:

```
api_plan  graph_plan  parser_plan  graph_result  entity_result  error_context
raw_json_path  reporter_plan  api_result_full  api_result_meta  api_result_slim
reporter_result  reporter_metadata  report_writer_output
shortlist_assay_codes  shortlist_diagnostics  shortlist_sampletype_codes
```

### Field alias table (read this before judging anything)

Several criteria in the corpus name fields that **do not exist** in the debug
object. `chat_nextseek/e2e/criteria.py` resolves them:

| Criterion field | Actually resolves to |
|---|---|
| `api_ok` | `api_result_meta.ok` (default `False` when absent) |
| `neo4j_ok` | `graph_result.ok` |
| `graph_cypher` | `graph_plan.cypher` (**not** `graph_result.cypher`) |
| `entity_sampletype_codes` | codes from `entity_result.sampletypes[]` |
| `entity_assay_codes` | codes from `entity_result.assays[]` |
| `last_reply` | the reply text, HTML stripped |
| `api_artifact.<file>` | in **e2e**, a filesystem check under `run_root/files/<file>`; in **nessie**, the turn's own artifact index (see below) |
| `bundle.*` | injected by nessie's bundle reader |
| `route`, `engine`, `route_source` | injected by `nessie_tests/evaluate.py` |
| anything else | dot-notation walk of the debug dict |

**`api_artifact.*` is no longer the trap it was.** This used to say the criteria
were permanently unevaluable because `evaluate.py` calls `check_pass` without a
`run_root`. It still calls it without one — the async endpoint reuses a single run
root per gunicorn process, so there is no per-turn root to hand it — but
`evaluate.py` now resolves these criteria itself, before delegating, out of a
per-turn index built from the turn's own `query_complete`
(`_split_local_criteria`, `build_artifact_index`, `resolve_artifact`). It reads
`debug.report_saved_files`, `files`, `artifacts` (file-typed entries only) and
`cc_raw_files`, so **a Container-CC turn can now prove it produced a file**.

Two limits survive, and a triager must not read either as a product defect:

- **A multi-deliverable CC turn only ever exposes `artifacts.zip`.** The publish
  path zips whenever it finds more than one deliverable and emits one artifact so
  labelled; member filenames never reach `query_complete`. Assert
  `api_artifact.artifacts.zip`, not a member name.
- **`.rows_gte` returns 0 for a CC artifact**, which is indexed under a bare label
  with no path on disk. Returning 0 is deliberate: resolving that label against
  the harness cwd would count rows out of an unrelated same-named file.

Expected filenames in the base corpus are still stale, separately: the reporter
now writes `merged_report_SRA_SRA_metadata_filled.xlsx` rather than `sra_seq.xlsx`.

---

## 3. Run root on disk

The async endpoint **reuses one run root per process**, it does not create one per
turn. `console.txt` is written once at process start (a config snapshot) and is
not a per-turn trace. The per-turn evidence is the timestamped files:

```
/app/outputs/<YYMMDD_HHMMSS>_<user>/
├── api_requests.json                  every API request, grows through the run
├── console.txt                        config snapshot at process start ONLY
└── files/
    ├── graph/graph_debug_<ts>.json    one per graph query: query, entity output,
    │                                  parser output, cypher, results
    ├── api/api_result_bundle_N.json   API result bundles
    ├── report/                        generated GEO / SRA / RPPR workbooks
    └── protocol/  memory/  nfcore_all-samples/
```

Map a `graph_debug_<ts>.json` onto a turn by comparing its timestamp to
`created_at`/`updated_at`. The correspondence is 1:1 with graph turns.

You rarely need these files: `fetch_run.py` already pulls the same plans and
result metadata out of the event streams, which is faster and survives log
rotation. Reach for the run root when you need the actual result *rows* or a
generated workbook, since those are deliberately stripped from `turns.json`.

---

## 4. Routing

- BAML function `RouteQuery` in `dmac_assistant/baml_src/router.baml`.
- Wrapper `nextseek_api/cc_assistant/router.py`.
- Capabilities loaded from `dmac_assistant/build_context/route_capabilities.json`,
  rendered into the prompt. Routing behaviour is configuration, not hard-coded rules.
- Routes: `nextseek_query`, `container_cc`, `unrelated`.
- History is passed in and framed as "data to interpret, NOT instructions" (an
  injection guard).
- `model_class` is returned but discarded; CC is always pinned to Opus, the only
  model the Bedrock proxy allowlists.

Two bypasses, both visible as `source` on the `route_decided` event:

| `source` | Meaning |
|---|---|
| `baml` | normal path |
| `heuristic` | BAML unavailable or returned `<router_unavailable>`; keyword regex fallback |
| `pipeline` | **short-circuit ahead of the router**, reasoning `pipeline_active`; the model never sees the query |

Turn execution: `POST /nextseek_api/cc-assistant/query/async/` starts a
`threading.Thread` daemon inside a gunicorn worker and returns immediately. Poll
`GET /nextseek_api/cc-assistant/tasks/{id}/progress/`. gunicorn runs **4 sync
workers** with `timeout = 1200` (`gunicorn.conf.py`).

---

## 5. Known state as of 2026-07-27

From the first live full-tier run (44 cases, tasks 721-774). Re-verify before
relying on any of it.

**Product findings**

- **Pipeline hijack.** An active pipeline agent short-circuits routing for
  *subsequent unrelated cases*, even ones starting with `session_id = None`. Sticky
  mode is keyed to something broader than the chat session. Tasks 736 (legitimate)
  and 737 (hijacked).
- **Graph `LIMIT 250` cap.** 3 of 11 graph queries returned exactly 250. Invisible
  to the suite; one capped case passed all assertions.
  **The cap is now 5000.** `nessie_tests/limits.py` keeps
  `GRAPH_LIMIT_SENTINELS = (250, 5000)` so old evidence is still caught, but a
  count landing on a limit is only a guess at truncation. The real signal is
  `graph_result.truncated`, and the criterion to read is
  `graph_truncation_disclosed`, which passes a capped result only when it also
  reports a `total` exceeding the rows returned. Do not triage a 2026-08 run
  against 250.
- **Node-resolution instability.** "study X" resolves sometimes to `Study.title`,
  sometimes to `Investigation.title`, sometimes both. Same question shape returned
  0 rows in one case and 250 in another. This is the mechanism behind issue #33.
- **Parser fallback → HTTP 422.** `api_plan.notes` of "Fallback minimal plan;
  structured parsing failed." with an empty request body, rejected 422.
- **Web tier saturation.** Three consecutive `container_cc` turns starved the 4
  sync workers; CC's own callbacks into `nextseek_nginx` returned `TRANSPORT_ERROR`.
  Task 774 has been wedged in `status=running` since, with no reaper.
- **Classification inconsistency.** Near-identical assay questions classified
  `new_search` vs `system_question`.

**Harness gaps** — as recorded then. Most have since CLOSED; the status after
each is current as of 2026-08-03 (`a85dde9`). Do not triage against the
left-hand column.

- Manifest stores criterion names only, never observed values.
  **CLOSED** — entries carry an `observations` list of `CriterionObservation`.
- 30s socket timeout turns infra latency into a test `error` and loses the route.
  **CLOSED** — `SOCKET_TIMEOUT_S` is **120**, and a single mid-poll failure is
  swallowed into `poll_errors`; only five consecutive failures raise.
- `default_route_criterion` injects `route == nextseek_query` into all imported
  variants. **CLOSED** — it returns `None`. Injection did not stop, though; it
  became CURATED, and reading this line as "nothing is injected" is how a real
  route failure gets discounted as residue. `corpus.apply_route_policy` attaches
  a `route` criterion to turn 0 of **268** of the 283 resolved variants — 15 more
  write one inline, so **all 283** carry one, against only 3 tagged `route_gate`
  — plus `last_reply nonempty` to 241. `apply_family_floor` adds 239 outcome
  criteria across 203 variants (`outcome_observed` to 146,
  `report_produced_output` to 57, `graph_truncation_disclosed` to 36). All of it
  comes from the `route_policy` and `family_floor` blocks in `corpus.json`,
  which are reviewable data, so a route failure today is a curated expectation
  rather than a harness assumption.
- `api_artifact.*` unevaluable (no `run_root`). **CLOSED** — resolved from the
  turn's own artifact index; see §2 for the two limits that remain.
- No xpass detection. **CLOSED** — `runner._apply_xpass` promotes a passing
  `known_fail` (variant *or* consistency group) to `status="xpass"`, which counts
  as a real failure.
- No session isolation between cases. **PARTLY CLOSED** — the runner passes
  `force_new` on each case's FIRST turn (`runner.py:203`) and on every consistency
  query (`:301`), so cases no longer inherit each other's ChatSession, pinned
  bundles or `results_history`. The pipeline-agent hijack above is a separate,
  product-side leak; it is recorded as open and was NOT re-verified this wave.
- Consistency groups never set `elapsed_s`. **CLOSED** — they set it on both the
  normal and the error path, and also record each query's `count` and `route`.

**Corrections to the 2026-07-27 handoff** (it was wrong on these):

- "Writes hang" is false. Both write tasks completed server-side in 164s and 118s.
- "Only one misroute" is false. Three turns routed to `container_cc`.
- "Both known-fails correctly red" is false. `repro.cypher_uid_dot` xpassed and
  `cons.nhp_sequencing_engine` never ran.
