# graph_search follow-up 1 (Nessie on the metadata graph): implementation plan

> **For agentic workers:** use superpowers:test-driven-development inside each task: write the failing test, run it,
> make it pass, run the area again. Steps use checkbox (`- [ ]`) syntax. Increment 1 (tasks N0 to N6) is built on
> this branch; tasks N7 onwards are planned only and wait for operator approval of the spec.

**Goal:** every Nessie graph read is scoped to the caller and read-only, and the graph agent sees the live v1.1
catalog as compact text, checked per label.

**Architecture:** the API host resolves a `GraphScope` from `request.user` and puts it on the per-request config copy;
the engine refuses any graph read without one. A lazy, process-level catalog reader in `chat_nextseek/graph_catalog.py`
replaces the `keys(n) LIMIT 200` file cache; a pure renderer in `graph_context.py` turns it into variant (b); the
property guard in `agents/graph.py` reads the same snapshot.

**Spec:** `docs/superpowers/specs/2026-09-15-graph-search-nessie-design.md`. Graph schema: `docs/neo4j-schema.md`
("v1.1"). POC spec section 11.1.

## Global constraints

- Worktree `wt-gs-nessie`, branch `feat/graph-search-nessie`, from `feat/graph-search` at `4b3e087a`. Push only this
  branch, after a scan of the diff for emails, home paths and tokens.
- Never edit `nextseek_api/graph_sync/*`, `nextseek_api/graph_search/*`, `nextseek_api/services/graph_search.py`,
  `ci/routes.py`, `nextseek_api/urls.py` or `docker/scripts/entrypoint.sh` (follow-up 2 and the POC own them). Read
  the catalog through Cypher only.
- Tests only in throwaway containers, one at a time; never against the live stack. No paid Nessie runs, no
  rebuilds, no SSH.
- Public repository: no credentials, emails, personal names or home paths in tracked files. No em-dashes.
- Stage files by name. Conventional commits with a module scope, each ending with exactly:

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017dhzDS7bs3wKsyxWgTYtkB
```

## Commands used throughout

```bash
# Django lane: the checkout's engine source first on the path, read-only mount, SQLite test settings
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none --memory 2g -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/src:/src/NessieAI/chat_nextseek/src:/src/NessieAI/dmac_assistant/src \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider

# Docs map
python3 ci/docs_map.py
```

## File ownership (increment 1)

| File | Task |
|---|---|
| `NessieAI/chat_nextseek/src/chat_nextseek/config.py` | N0, N3 |
| `NessieAI/chat_nextseek/src/chat_nextseek/graph_scope.py` (new) | N1 |
| `NessieAI/chat_nextseek/src/chat_nextseek/cypher_text.py` (new) | N1 |
| `NessieAI/chat_nextseek/src/chat_nextseek/helpers/tools/neo4j.py` | N1 |
| `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py`, `agents/planner/tools.py`, `NessieAI/ns/granular.py` | N1 |
| `NessieAI/chat_nextseek/src/chat_nextseek/reports/runners.py` | N1 |
| `nextseek_api/assistant/graph_scope.py` (new), `nextseek_api/services/assistant.py`, `nextseek_api/services/cc_assistant.py`, `nextseek_api/services/evaluator.py`, `NessieAI/cc/turn.py`, `NessieAI/ns/retry.py` | N2 |
| `nextseek_api/services/entity_tree.py` | N2 |
| `NessieAI/chat_nextseek/src/chat_nextseek/graph_catalog.py` (new), `mcp_server.py` | N3 |
| `NessieAI/chat_nextseek/src/chat_nextseek/graph_context.py` (new), `prompts/graph_schema_structure.txt` (new), `agents/graph.py`, `agents/system.py` | N4, N5 |
| docs: the spec, this plan, `.gitignore`, `docs/INDEX.md`, `NessieAI/chat_nextseek/.gitignore`, `.dockerignore` | N6 |

## Increment 1

### Task N0: A0, per-source context export status

**Files:** `config.py` (`_connect_db`, `_fetch_context_files_from_db`, `_ensure_context_files`,
`get_config_snapshot`); tests `NessieAI/tests/chat_nextseek/test_context_export_status.py` (new),
`test_config_context_freshness.py` (updated to the per-source markers).

- [ ] Test: a bare config (`ChatConfig.__new__`) whose fetch exports sampletypes and projects but not assays reports
  `CONTEXT_EXPORT_STATUS` with `ok` true, true, false, the assays error text, rows and seconds; markers exist for two
  sources only; a second load calls the fetch with `sources=["assays"]` only.
- [ ] Test: no database connection marks all three failed with the connection error and writes no marker.
- [ ] Test: a failed source is logged at WARNING through `logging` (caplog), whatever `CONFIG_VERBOSE` is.
- [ ] Test: `get_config_snapshot()["context_export"]` carries the status.
- [ ] Implement: `_CONTEXT_SOURCES` table (source to table and target files); `_fetch_context_files_from_db(env,
  sources=None)` exports the named sources and fills `self._context_export_detail`; `_connect_db` keeps
  `_last_db_error` (exception class and message; never a password); markers `.context_db_refresh.<source>`.
- [ ] Run the area; commit `feat(chat_nextseek): report the context export per source (A0)`.

### Task N1: A1 engine side, scope and read-only on every graph path

**Files:** `graph_scope.py` (new), `cypher_text.py` (new: `mask_cypher`, moved from `agents/graph.py`, which keeps
`_mask_cypher` as an alias), `helpers/tools/neo4j.py`, `orchestrator.py::_execute_graph_turn`,
`agents/planner/tools.py::_plan_tool_graph_query`, `NessieAI/ns/granular.py::_graph`, `reports/runners.py`; tests
`test_graph_scope.py`, `test_cypher_write_check.py` (new), `test_neo4j_total_probe.py`, `test_investigation_report.py`,
`NessieAI/tests/ns/test_granular_endpoints.py` (updated).

- [ ] Test `graph_scope`: `GraphScope.admin()`, `for_projects([3, 1, 3])` sorts and dedupes; `scope_of` ignores a
  non-`GraphScope` value; `with_graph_scope` copies and leaves the original untouched; `graph_turn_refusal` is None
  for an admin, a reason for no scope and for a non-admin.
- [ ] Test the write check: `CREATE`, `MERGE`, `SET`, `DELETE`, `DETACH DELETE`, `REMOVE`, `DROP`, `LOAD CSV`,
  `FOREACH`, `CALL apoc.refactor.mergeNodes`, `CALL dbms.listConfig`, `CALL db.labels`, `SHOW USERS`, `USE system`
  and a backticked procedure name are refused; `CALL db.index.fulltext.queryNodes(...)`, `CALL () { ... }`,
  `CALL { ... }`, and a literal `'Data Set'` or `` `SET_ID` `` pass.
- [ ] Test `tool_neo4j_query` against a fake driver: refused with no scope, with a non-admin scope, with a
  `__scope_projects` parameter; for an admin the statement runs through `execute_read` (the fake records it) and the
  probe too; the fake session's `run` is never called directly.
- [ ] Test `run_scoped_read`: binds `__scope_admin`/`__scope_projects`; refuses a statement without both; a
  non-admin with no projects gets `ok`, zero rows, and no driver call; refuses caller parameters named `__scope*`.
- [ ] Test the three graph turn entry points refuse before calling `graph_agent` for a non-admin and for no scope.
- [ ] Test the reporter's two Neo4j reads carry the scope clause and run through `run_scoped_read`.
- [ ] Implement; keep `tool_neo4j_query(config, cypher, parameters=None)` (portable contract).
- [ ] Run the area; commit `feat(chat_nextseek): scope and read-only on every Nessie graph path (A1)`.

### Task N2: A1 host side, the scope seam and entity_tree lineage

**Files:** `nextseek_api/assistant/graph_scope.py` (new: `graph_scope_for`, `attach_graph_scope`),
`nextseek_api/services/assistant.py` (`query`, `query_async`, `_granular_chat_config`),
`nextseek_api/services/cc_assistant.py::_start_task`, `NessieAI/cc/turn.py::start_task` (`graph_scope` keyword),
`nextseek_api/services/evaluator.py`, `NessieAI/ns/retry.py::run_retry` (`graph_scope` keyword),
`nextseek_api/services/entity_tree.py`; tests `NessieAI/tests/api/test_graph_scope_host.py`,
`nextseek_api/tests/test_entity_tree_scope.py` (new).

- [ ] Test `graph_scope_for`: superuser to admin; a member to `for_projects`; `ScopeUnavailable` or a database
  error to None.
- [ ] Test each seam puts a scope on the config the engine receives (patched `run_query`, `run_op`, `start_task`),
  and never on the singleton; the PROD identity check still sees the singleton.
- [ ] Test lineage for a non-admin: an anchor outside the caller's projects reads "Sample not found"; nodes outside
  them and edges touching them are dropped; an admin sees everything; the driver is called with READ routing.
- [ ] Test `edges` and `edge_attributes` use READ routing.
- [ ] Implement; run `NessieAI/tests/api`, `NessieAI/tests/ns`, `NessieAI/tests/cc/test_shared_memory_symmetry.py`,
  `nextseek_api/tests/test_services_entity_tree.py` and the new tests; check `NessieAI/tests/api/test_nessie_boundaries.py`
  (no new back-edge).
- [ ] Commit `feat(nextseek_api): resolve Nessie's graph scope per request (A1)`.

### Task N3: A2, the live catalog reader

**Files:** `graph_catalog.py` (new), `config.py` (drop the seven fetch and ensure methods; `NEO4J_SCHEMA`,
`PROTOCOL_SCHEMA`, `ASSAY_SAMPLE_CONNECTIONS` read the committed JSON only; snapshot), `mcp_server.py`; tests
`test_graph_catalog.py` (new).

- [ ] Test with a fake driver: the snapshot reads META, INDEX and GUARD once; a second call within 60 s reads
  nothing; after 60 s with the same hash it reads META only; with a new hash it re-reads INDEX and GUARD and drops
  type details.
- [ ] Test: two configs with different `NEO4J_URI` get separate snapshots.
- [ ] Test: no `GraphMeta`, `schema_version` not 1.1, a driver error, or an unconfigured URI raise
  `CatalogUnavailable`; the failure is remembered for 60 s (no second connect).
- [ ] Test: type details are fetched once per (hash, type, mode) and expire after 10 minutes; scoped mode passes
  `$projects`; structure mode is used for a non-admin when no `USED_IN` exists.
- [ ] Test: every statement runs READ with a timeout; no file appears in a temporary `CONTEXT_DIR` after
  `ChatConfig` construction plus a snapshot (construction patched to avoid LLM clients).
- [ ] Implement; run the area; commit `feat(chat_nextseek): read the graph catalog live, cached on catalog_hash (A2)`.

### Task N4: A3, the variant (b) renderer

**Files:** `graph_context.py` (new), `prompts/graph_schema_structure.txt` (new), `agents/graph.py::graph_agent`,
`agents/system.py::system_agent`; tests `test_graph_context.py` (new), `test_graph_property_guard.py`,
`test_graph_canonical_uid.py`, `test_graph_refine.py` (kept green through the fallback).

- [ ] Test the index line format, deprecated types omitted, zero-sample types flagged, counts only for an admin.
- [ ] Test a section: the K most-filled attributes in full, `also filled:` names, backticks, `(undeclared)`, units,
  ranges, values over 60 characters skipped, meaning cut to its first clause.
- [ ] Test budgets: three synthetic 190-attribute types with long meanings and ten values each stay within 32,768
  bytes; a single type stays in full when it fits.
- [ ] Test non-admin output has no counts and no values without usage, and scoped counts with usage.
- [ ] Test the structure file names only labels and relationship types that `docs/neo4j-schema.md` v1.1 lists, and
  never `CHILD_OF` except to say it does not exist.
- [ ] Test `graph_agent` sends the rendering (not the JSON) when the catalog is live, and the JSON when it is not;
  resolved codes come from the plan first; the vocabulary blocks are gated as the spec says.
- [ ] Implement; run the area; commit `feat(chat_nextseek): render the graph context from the catalog (A3)`.

### Task N5: A4, the per-label property guard

**Files:** `agents/graph.py`; tests `test_graph_catalog_guard.py` (new).

- [ ] Test `TIS.Sequencer` rejected on `(s:T_TIS)`, accepted on `(s:T_D_SEQ)`; `WHERE s:T_TIS` labels the variable;
  plain `Sample` uses the union; system properties always pass; relationship properties per type; backticked names
  and map projections checked; unknown `T_` labels reported; `db.index.fulltext.queryNodes(` and `date.truncate(` are
  not properties; literals and parameters ignored.
- [ ] Test `graph_agent` repairs once and then returns the empty plan naming `TIS.Sequencer`.
- [ ] Implement; run the area; commit `feat(chat_nextseek): check Cypher properties per label against the catalog (A4)`.

### Task N6: docs and hygiene

- [ ] `.gitignore` negations and `docs/INDEX.md` rows for the spec and this plan; `python3 ci/docs_map.py` clean.
- [ ] Ignore the per-source markers in `NessieAI/chat_nextseek/.gitignore` and `.dockerignore`.
- [ ] `NessieAI/chat_nextseek/CLAUDE.md` landmine on the context export updated to the per-source markers; the graph
  snapshot is no longer written into `context/`.
- [ ] Commit `docs(graph_search): spec and plan for Nessie on the metadata graph` (the docs commit may come first).

### Increment 1 gate

- [ ] Every area touched passes in the Django lane: `NessieAI/tests/chat_nextseek`, `NessieAI/tests/ns`,
  `NessieAI/tests/api`, `NessieAI/tests/cc` (Django-importing modules only where the lane supports them),
  `nextseek_api/tests/test_services_entity_tree.py`, the new host and lineage tests. Compare failures with a run of
  the same paths on `origin/feat/graph-search`; report any difference.
- [ ] `python3 ci/docs_map.py` clean; `python3 scripts/validate_viewset_conventions.py` clean.
- [ ] Diff scanned for emails, home paths and tokens; push `feat/graph-search-nessie` only.

## Later increments (planned, not built)

### Task N7: A5, typed entities (increment 2)

`schemas/entity.py` (additive `attributes`, `project_ids`), `agents/entity.py` (deterministic validation and spelling
expansion beside `lab_codes`), `helpers/tools/catalog_match.py` (graph candidates plus code hits),
`helpers/tools/catalog_semantic.py` (`ATTRIBUTE_INDEX`), `prompts/entity_agent.txt`. Needs request R2. Accept: the 17
unreachable types resolve; "lung" yields `IN ['Lung', 'lung']`; `test_portable_contract.py` unchanged;
`test_shortlist_recall.py` gains key cases.

### Task N8: A6, attribute filters to graph_search (increment 2)

`schemas/router.py::ParserFilters.attribute_filters`, `agents/api.py`, `prompts/api_agent.txt`; one
`is_sample_search_endpoint` replacing the 15 `advanced_search` literals in `nextseek_api.py`, `parser.py`,
`planner/agent.py`, `planner/tools.py`, `_batch_upload_client.py`; graph_search in `min_api_endpoints_enriched.json`,
`nextseek_api.yaml`, both `read_safe_endpoints.json` copies (the drift guard's read-safety audit) and
`_READ_POST_PATHS`; page size 100. Accept: the retry ladder and timeout apply to graph_search; a non-admin's sample
question is answered through it.

### Task N9: A7, prompts (increment 3)

`graph_agent.txt`, `parser_core_routing.txt`, `multi_parser_agent.txt`, `planner_agent.txt`, `capabilities.md`,
`min_graph_schema.json`: metadata on the node, exact matching separate from title `CONTAINS`, whole-node returns
forbidden (ids only, hydrate vetted fields), the parser gets the type index. Remove the precedence line from the
structure text. Corpus family descriptions are live router input: change them only with the operator.

### Task N10: A8, follow-ups re-query (increment 3)

`agents/memory.py::_load_memory_json_payload`, `_format_memory_coder_answer` (pass `total`), `_execute_graph_turn`,
`_plan_tool_coding_filter`, `_dispatch_recall`, `ns_turn_context.py::from_bundle`: store the predicate, re-query facets
when `total` exceeds the rows held, disclose partial coverage.

### Task N11: A9, the CC graph-schema op (increment 4)

`/add-cc-op` for `nextseek-graph-schema [--types ...]` serving the rendering through the sidecar; delete the plugin
`neo4j_schema.json` and `min_graph_schema.json`; update `MANIFEST.md`, `SKILL.md`, `container/CLAUDE.md`,
`PORT-EVIDENCE.json` and the drift-guard tests. Accept: a catalog change needs no cc-agent rebuild.

### Task N12: A10, the corpus (increment 4)

Rewrite the 56 `api_plan.endpoint` criteria; keep the numeric `last_reply` regexes as the oracle; add full-result
facet variants (over 1,000 rows) and attribute-conditioned graph variants; tag HiBayes evidence by engine version.

### Task N13: statistics and sensitivity (with follow-up 2's B3)

Render `USED_IN` statistics for non-admins (already read by N3) once follow-up 2 writes them, and suppress the values
of any `sensitive` attribute (request R3). Accept: a non-member's rendering shows no foreign counts or values.

### Task N14: a scope rewrite for non-admin Cypher (optional, last)

Only after an adversarial suite (path variables, `nodes()`, `UNION`, subqueries, variable-length interiors, aliasing)
exists and passes. Until then a non-admin's free Cypher stays refused.

## Self-review

- Every increment 1 item (A0 to A4) has a task, a test list and an acceptance line from the spec; A5 to A10 have
  tasks with their acceptance.
- No task edits a file owned by follow-up 2 or the POC; R1 to R5 in the spec carry what this work needs from them.
- Fail-closed behaviour is tested at each entry point, not only in the tool.
