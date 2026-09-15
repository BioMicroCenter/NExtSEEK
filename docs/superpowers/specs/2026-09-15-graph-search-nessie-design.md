# graph_search follow-up 1: Nessie on the metadata graph

- Date: 2026-09-15
- Branch: `feat/graph-search-nessie`, cut from `feat/graph-search` at `4b3e087a`
- Status: draft for operator approval. Increment 1 (A0 to A4) is built on the branch; A5 to A10 are planned only.
- Tracking: none yet. File an issue per `docs/ISSUE-CONVENTIONS.md` after approval.
- Builds on: the POC spec `docs/superpowers/specs/2026-09-14-graph-search-poc-design.md` (section 11.1 is this
  scope; sections 3 to 7 are the graph and endpoint this builds on) and `docs/neo4j-schema.md` ("v1.1").
- Plan: `docs/superpowers/plans/2026-09-15-graph-search-nessie.md`.
- The recon behind it (two stages, 2026-09-14) stays outside the repository because it cites local data. Numbers
  below are dated measurements from it; re-measure before relying on one.

## 1. Goal

Let Nessie read the v1.1 graph safely and usefully: every graph read is scoped to the caller and read-only, and the
graph agent sees the real catalog instead of a `keys(n) LIMIT 200` scan. Later increments let Nessie answer
attribute questions through `graph_search` and the catalog.

Not the goal:
- Changing `graph_sync`, `graph_search` or the catalog writer. Follow-up 2 and the POC own them; this work reads the
  catalog through Cypher only.
- Writing per-project statistics. Follow-up 2 writes `(:Attribute)-[:USED_IN]->(:Project)`; Nessie reads it when it
  exists and works without it.

## 2. What the recon found (short)

| Fact | Consequence |
|---|---|
| No Nessie graph path is scoped. `tool_neo4j_query` runs model Cypher with the service account in write mode; the NS graph turn, the planner, the CC `graph` op, the reporter and the `entity_tree` lineage read all reach it or the driver unscoped | with metadata on the node, any user reads every project's metadata. Scope first (A1) |
| The write-block regex scans unmasked text: it blocks the read-only `db.index.fulltext.queryNodes` and any literal containing SET or DELETE, and lets `apoc.refactor.*` through | apply it to masked text, with an allowlist of procedures, behind a read transaction |
| Nessie's graph context is one `keys(n) LIMIT 200` scan written into the package `context/` directory, trusted by file mtime, shared by the PROD config, frozen per worker, and sent whole (26.4 KB) on every graph call | replace it with a live catalog read cached on `GraphMeta.catalog_hash`, writing nothing (A2) |
| Variant (b) (structure, a type index, at most 3 resolved types at 25 attributes) is about 30.5 KB on the merged data; every type in full is 459 KB | render variant (b) as compact text (A3) |
| `known_node_properties` is a type-blind union and ignores backticked names | check property names per label against the catalog (A4) |
| The context export writes one day marker when any one of three exports succeeds, and prints only under `CHAT_NEXTSEEK_CONFIG_VERBOSE`; locally it fails with MySQL 1045 unseen | report success per source (A0) |
| The entity agent resolves no attribute keys; 17 of 118 merged types can never be emitted | typed entities (A5, increment 2) |

## 3. Decisions

Questions Q13 to Q16, Q20 and Q21 are from the recon's operator list. Each has a recommendation, adopted by this
spec and marked **operator to confirm**.

| # | Question | Decision |
|---|---|---|
| Q13 | Case variants and the long tail: resolve-time expansion or a shadow property? Where do spellings come from? | Resolve-time expansion in the entity step (A5): a term becomes `IN [every stored spelling]`, which keeps graph_search exact. Spellings come from a full distinct-value list per non-sensitive (type, attribute) pair with at most 200 distinct values (about 2,300 of 2,489 local pairs), written by follow-up 2 (request R2); the rest stay fulltext keywords. **Operator to confirm** |
| Q14 | `attributes[]` on the entity output: POC or follow-up 1? | Follow-up 1, increment 2 (A5), additive, validated against the catalog. **Operator to confirm** |
| Q15 | Assay catalog and project aliases into the graph now? | Later. `assays_db.json` and `projects_db.json` stay inputs; a TCGA `projects_context` row is added before any Nessie TCGA test; `pi` never goes on the graph. **Operator to confirm** |
| Q16 | Free Cypher for non-admins, and Nessie's identity for graph reads | Non-admins get no free Cypher: `graph_search` (A6) and fixed, server-written scoped reads only, until a scope rewrite passes an adversarial test. Every read runs as the per-request user's scope, resolved from MySQL membership (the POC resolver), never the service account's. In increment 1 a non-admin's graph_query turn is refused with an explanation before any LLM call. **Operator to confirm** |
| Q20 | K=25? Trim meanings? Index the zero-sample and deprecated types? Does the parser get the index? | K=25 attributes in full per resolved type, at most 3 types; meanings trimmed to their first clause (at most 120 characters); zero-sample types listed and flagged; deprecated types left out; the parser gets the type index in A7, together with the routing prompt change that makes it useful, not in increment 1. **Operator to confirm** |
| Q21 | Follow-ups: re-query the stored predicate or aggregate stored rows? Nessie page size? | Re-query the stored predicate when `total` exceeds the rows held (A8); Nessie asks graph_search for 100 rows a page (A6). **Operator to confirm** |

Design decisions for increment 1 (the recon's recommendations, adopted unless the operator objects):

| # | Decision | Why |
|---|---|---|
| D1 | The caller's scope is a `GraphScope {is_admin, project_ids}` set by the API host on the per-request config copy (`config.GRAPH_SCOPE`). The ViewSets resolve it from `request.user` with the POC resolver (`nextseek_api/graph_search/scope.py::resolve_scope`); the engine never resolves it and never reads it from the model, the request body or Cypher parameters | the engine stays free of new back-edges; one seam for every route |
| D2 | Fail closed: no scope, an unresolvable caller, or a non-admin asking for free Cypher is refused. A non-admin with no projects gets zero rows and no query | a missing scope must never mean "unscoped" (`nextseek_api/CLAUDE.md`) |
| D3 | Every Nessie graph read runs in a READ transaction (`session.execute_read`, or `execute_query` with READ routing), so the server refuses writes; the write regex runs on masked text and allows exactly one procedure, `db.index.fulltext.queryNodes` | server-enforced read-only, and the keyword search graph_search uses becomes legal |
| D4 | Server-written Cypher (the reporter) runs through `run_scoped_read`, which binds `$__scope_admin` and `$__scope_projects` itself and refuses a statement that does not use both, or parameters that try to set them | scope by construction for fixed reads |
| D5 | `entity_tree/lineage` is scoped after the query against MySQL `projects_samples` (anchor hidden when not visible, foreign nodes and their edges dropped), so it works on v1.0 and v1.1 graphs alike. `entity_tree/edges` and `edge_attributes` return type-level facts (parent type, child type, assay title), no sample identity and no counts; they stay unscoped, like the assay-connection vocabulary every user already gets, and run READ. **Operator to confirm** | the lineage read returns samples; the edge reads are catalog-level |
| D6 | The catalog reader is lazy (nothing runs at `ChatConfig` construction), cached per process on `(NEO4J_URI, NEO4J_DATABASE)`, re-checks `GraphMeta.catalog_hash` at most every 60 s, and writes no file. Vocabulary, protocol titles and assay connections are read the same way with a one-hour TTL and no `LIMIT 300` | removes the boot scan, the mtime defect, the PROD collision and the package writes together |
| D7 | Fallback: when the graph is unreachable, or has no `GraphMeta` with `schema_version` 1.1, Nessie uses the committed `context/neo4j_schema.json` and the old type-blind guard, as today. A failed read is remembered for 60 s | a v1.0 graph (production today) keeps working; an outage costs one timeout a minute, not one a turn |
| D8 | The structure section is hand-owned text in the package (`prompts/graph_schema_structure.txt`), kept consistent with `docs/neo4j-schema.md` v1.1 by a test | the runtime cannot read `docs/`; the doc stays the record |
| D9 | Rendering is compact text with a hard 32 KB budget: when over, K drops (25, 15, 10, names only) before a resolved section is dropped | the budget holds whatever types resolve |
| D10 | Non-admin rendering carries no sample counts and no values unless per-project usage (`USED_IN`) exists, in which case the scoped form (the recon's Q2) is used; undeclared attributes are admin-only | global statistics leak across projects |
| D11 | The context export records, per source (table), whether it succeeded, how many rows, how long, and the error; each source has its own day marker, and only failed or stale sources are re-read. The status is on the config (`CONTEXT_EXPORT_STATUS`), in `get_config_snapshot`, and logged through `logging` so the `CONFIG_VERBOSE` redirect cannot hide it | a partial export is visible |
| D12 | The CLI and the MCP server supply no scope, so their graph turns are refused until a later change gives them an explicit operator scope | fail closed; they are single-operator tools |

## 4. Increment 1: what is built

Paths under `NessieAI/chat_nextseek/src/chat_nextseek/` are written short.

### 4.1 A0: an honest context export

`config.py`: `_fetch_context_files_from_db(env, sources=None)` exports only the named sources (`sampletypes`,
`assays`, `projects`) and records per source `{table, ok, rows, seconds, error}`. `_connect_db` keeps the last
connection error text (never a credential). `_ensure_context_files` re-reads a source when its marker
`.context_db_refresh.<source>` is not dated today (UTC) or one of its files is missing, writes the marker only for a
source that exported, and sets `CONTEXT_EXPORT_STATUS`. A failed source is logged at WARNING. The old single marker is
ignored.

Accept: with the assays table failing, `CONTEXT_EXPORT_STATUS["assays"]["ok"]` is false with its error, the other two
are true, only the assays marker is missing, and the next load re-reads assays alone.

### 4.2 A1: scope on every graph path

New engine module `graph_scope.py`: `GraphScope` (frozen; `admin()`, `for_projects(ids)`), `scope_of(config)`,
`with_graph_scope(config, scope)` (a shallow copy; the shared singleton is never mutated) and
`graph_turn_refusal(config)` (the user-facing reason, or None).

Host side, new `nextseek_api/assistant/graph_scope.py`: `graph_scope_for(user)` maps the POC `Scope` to a
`GraphScope` (None on `ScopeUnavailable` or any error) and `attach_graph_scope(config, user)`. Called by
`AssistantViewSet.query`, `query_async` and `_granular_chat_config`, by `CCAssistantViewSet._start_task` (passed into
`NessieAI/cc/turn.py::start_task` as `graph_scope`), and by the evaluator retry (passed into
`NessieAI/ns/retry.py::run_retry`). Each attaches after the PROD-config identity check, which compares the singleton.

Engine side:

| Path | Change |
|---|---|
| `helpers/tools/neo4j.py::tool_neo4j_query` | refuses with no scope or a non-admin scope; refuses parameters named `__scope*`; masked write check; READ transaction with a 60 s timeout; the LIMIT probe runs in its own READ transaction |
| `helpers/tools/neo4j.py::run_scoped_read` (new) | for server-written Cypher: binds the two scope parameters, refuses a statement missing either, zero rows and no query for a non-admin with no projects |
| `orchestrator.py::_execute_graph_turn`, `agents/planner/tools.py::_plan_tool_graph_query`, `NessieAI/ns/granular.py::_graph` | refuse before any agent call when `graph_turn_refusal(config)` returns a reason |
| `reports/runners.py` (`_neo4j_investigation_sample_uuids`, `run_project_published_report`) | fixed Cypher gains `($__scope_admin OR any(p IN s.project_ids WHERE p IN $__scope_projects))` and runs through `run_scoped_read` |
| `nextseek_api/services/entity_tree.py` | lineage scoped per D5; all three reads use READ routing |

On a v1.0 graph, Sample nodes carry no `project_ids`, so a non-admin's scoped reporter read returns nothing: fail
closed, by design.

Accept: a non-member sees 0 foreign rows (reporter and lineage tests with two projects); a write sent through
`tool_neo4j_query` is refused by the regex and, independently, would run in a READ transaction; a fulltext call
passes the regex.

### 4.3 A2: the catalog reader

New module `graph_catalog.py`. Queries, all read-only:

| Name | Reads | Used for |
|---|---|---|
| `META` | `GraphMeta {schema_version, catalog_hash}` and whether any `USED_IN` edge exists | validity, cache key, D10 |
| `INDEX` | every SampleType: `title`, `label`, `name`, `clade`, `sample_count`, `deprecated`, attributes with values, all attributes | the type index |
| `GUARD` | per SampleType `label`, the titles of its attributes with values | A4 |
| `TYPES_ADMIN` | the resolved types in full (the recon's Q1): attribute `title`, `value_type`, `declared`, `needs_backticks`, `sample_count`, `meaning`, `unit_key`, `role`, and `top_values`, `top_counts`, `num_*`, `date_*` when present | admin sections |
| `TYPES_SCOPED` | the recon's Q2: usage summed over `$projects` from `USED_IN` | non-admin sections when usage exists |
| `TYPES_STRUCTURE` | declared attributes only, by `pos`, no counts | non-admin sections without usage |
| vocabulary | Investigation, Project and Study titles, published studies, DERIVED_FROM assay and protocol titles, assay connections | keyword-gated blocks |

`ChatConfig` loses `_fetch_neo4j_schema`, `_ensure_neo4j_schema`, `_ensure_schema_file`,
`_fetch_assay_sample_connections`, `_ensure_assay_sample_connections`, `_fetch_protocol_schema` and
`_ensure_protocol_schema`. `NEO4J_SCHEMA`, `PROTOCOL_SCHEMA` and `ASSAY_SAMPLE_CONNECTIONS` become the committed
JSON, read only, as the fallback. `get_config_snapshot` reports the catalog cache state without a network call.
`mcp_server.py` serves `neo4j-schema` as the rendering.

Accept: constructing a `ChatConfig` and running a graph turn writes nothing under `context/`; a config whose
`NEO4J_URI` differs reads its own graph (the cache key); a graph that is down or v1.0 yields the committed JSON.

### 4.4 A3: the renderer

New module `graph_context.py` (pure). The text has three parts:

1. **Structure** (`prompts/graph_schema_structure.txt`, about 3 KB): labels, relationships, system properties and the
   ten rules of the recon's draft, plus one precedence line (this section wins over any older instruction that
   Samples carry no metadata; A7 removes those instructions).
2. **Type index**, one line per non-deprecated type:
   `TIS :T_TIS "Tissue Sample" clade Source, 73,473 samples, 41 attributes with values` (admin) or without the counts
   (non-admin); `no samples` flags zero-sample types.
3. **Resolved types**, at most 3 (codes from the parser plan's `resolved` and `filters.sampletype_code`, else the
   entity output), each a header, the first sentence of its summary, curated parents and children, the K most-filled
   attributes in full (`- Organ [string] n=16,841 values: "Lung" 16,841, "lung" 5,893 -- <meaning>`) and one
   `also filled:` line of names, then the count of declared attributes that hold no value.

Used by `agents/graph.py::graph_agent` (replacing `json.dumps(NEO4J_SCHEMA, indent=2)`) and
`agents/system.py::system_agent`. Vocabulary goes in its own blocks: investigation and project titles always, study
titles and published studies when the question names a study or paper, assay titles and connections on the existing
assay words, protocol titles on the existing protocol words.

Accept: 3 of the largest merged-shape types render in at most 32 KB; no attribute appears that the catalog does not
list for that type; the rendering works with no `USED_IN`, no `top_values` and no meanings.

### 4.5 A4: the property guard

`agents/graph.py` gains a catalog guard: a variable's labels come from its patterns (`(s:Sample:T_TIS)`) and label
predicates (`WHERE s:T_TIS`). A `T_X` variable may read the Sample system properties and the attributes of X with
values; a plain `Sample` variable may read the union over all types; the other labels and relationship types have
their v1.1 property sets; a variable of unknown label is checked against everything. Backticked names
(`` s.`Catalog#` ``), map projections (`s {.Organ}`) and unknown `T_` labels are checked too; function and procedure
names (`db.index.fulltext.queryNodes(`) are not properties. The existing repair loop reports `TIS.Sequencer`. With the
catalog unavailable, the old `known_node_properties` union from the committed JSON applies.

Accept: `MATCH (s:T_TIS) WHERE s.Sequencer = 'x'` is rejected when TIS has no `Sequencer`, and passes on
`s:T_D_SEQ` when D.SEQ has it.

## 5. Later increments (scope only)

- **Increment 2: entities and routing.** A5 typed entities: additive `attributes[{sample_type, key, op, value,
  source}]` and `project_ids` on `EntityAgentOutput`, a deterministic validation and spelling-expansion pass beside
  `lab_codes`, graph SampleTypes as the type source (the 17 unreachable types resolve), an `ATTRIBUTE_INDEX` beside
  the semantic shortlist. A6 attribute filters to graph_search (`ParserFilters.attribute_filters` to `extensions`),
  the 15 `advanced_search` literals in 6 files folded into one `is_sample_search_endpoint`, graph_search in
  `min_api_endpoints_enriched.json`, `nextseek_api.yaml`, both `read_safe_endpoints.json` copies and
  `_READ_POST_PATHS`, page size 100.
- **Increment 3: prompts and follow-ups.** A7 prompt rewrites (`graph_agent.txt`, `parser_core_routing.txt`,
  `multi_parser_agent.txt`, `planner_agent.txt`, `capabilities.md`, `min_graph_schema.json`): metadata is on the
  node, exact matching, whole-node returns forbidden, the parser gets the type index. A8 follow-ups re-query the
  stored predicate when `total` exceeds the rows held, disclose partial coverage, and fix the graph-bundle recall gap.
- **Increment 4: CC and corpus.** A9 a `nextseek-graph-schema` Container-CC op (via `/add-cc-op`) serving the same
  rendering; the plugin `neo4j_schema.json` and `min_graph_schema.json` snapshots go. A10 the corpus: rewrite the 56
  `api_plan.endpoint` criteria, keep the numeric reply regexes as the oracle, add full-result facet and
  attribute-conditioned variants.
- **Moved here from the POC:** reading per-project statistics (`USED_IN`, D10 already reads them) and honouring a
  curated `sensitive` flag in the renderer (values of a sensitive attribute are never rendered).
- **A scope rewrite for non-admin Cypher** (the recon's clause injection over bound Sample variables, rejecting
  shapes it cannot prove safe) only after an adversarial test suite exists; until then D2 stands.

## 6. Requests to follow-up 2 and the POC

| # | To | Request |
|---|---|---|
| R1 | follow-up 2 | Stamp statistics freshness on `GraphMeta` (for example `stats_computed_at`), or fold statistics into `catalog_hash`. Today the hash covers structure only by design, so Nessie's per-type detail cache also expires by age (10 minutes) |
| R2 | follow-up 2 | For A5: a distinct-value list per non-sensitive (type, attribute) pair with at most 200 distinct values (for example `Attribute.values`), and `top_values`/`top_counts` per `USED_IN` edge after the `sensitive` filter and the email scrub |
| R3 | follow-up 2 | A `sensitive` flag on Attribute; Nessie never renders the values of one |
| R4 | POC | Keep `nextseek_api/graph_search/scope.py::resolve_scope(user) -> Scope` and `ScopeUnavailable` stable; Nessie's host seam imports them |
| R5 | follow-up 2 | Keep `SampleType.deprecated`, `sample_count`, `Attribute.sample_count`, `meaning`, `role`, `unit_key` and `needs_backticks` as `docs/neo4j-schema.md` v1.1 lists them; the renderer reads them by name |

## 7. Testing

- Django lane in a throwaway container over a read-only mount (`NessieAI/tests/README.md`), with the checkout's
  engine source first on `PYTHONPATH`. No live Neo4j or MySQL: the reader, the tool and the lineage scope are tested
  against fake drivers and patched SQL.
- New tests: `NessieAI/tests/chat_nextseek/test_graph_scope.py` (A1 engine), `test_cypher_write_check.py`,
  `test_graph_catalog.py` (A2), `test_graph_context.py` (A3), `test_graph_catalog_guard.py` (A4),
  `test_context_export_status.py` (A0); `NessieAI/tests/api/test_graph_scope_host.py` (host seam);
  `nextseek_api/tests/test_entity_tree_scope.py` (lineage). Updated: `test_neo4j_total_probe.py`,
  `test_config_context_freshness.py`, `test_investigation_report.py`, `test_granular_endpoints.py` where they pin old
  behaviour.
- Not verified in increment 1: `execute_read` refusing a write on the live Neo4j 2026.07.1 (the POC's gate E3 covers
  the same driver call for graph_search); the catalog queries against the live v1.1 graph (they `EXPLAIN` as
  `READ_ONLY` per the recon; costs unmeasured); any paid Nessie turn.

## 8. Risks

- **Behaviour change for non-admins.** Graph turns (lineage and structural questions) are refused for non-admins in
  increment 1, until A6 routes their sample questions to graph_search. The branch must not be deployed to a box whose
  non-admins rely on graph turns without the operator accepting that.
- **Prompt contradiction until A7.** `graph_agent.txt` still says Samples carry three properties. The structure
  section states it takes precedence, but the model sees both until A7 lands; do not judge answer quality on
  increment 1 alone.
- **v1.0 graphs.** Production runs a v1.0 graph: the fallback keeps today's context there, but a non-admin's scoped
  reporter read returns nothing, because v1.0 Samples have no `project_ids`.
- **Parser in a security role.** The masked write check is a second line; the READ transaction is the first.
- **Unmeasured costs.** The assay-title and connection reads scan every DERIVED_FROM edge (about 2M merged); they run
  lazily, at most once an hour per process.
