# graph_search proof of concept: design

- Date: 2026-09-14
- Branch: `feat/graph-search`, cut from `origin/dev` at `867aa100`
- Status: draft for operator approval (gate S). Built on a two-stage read-only recon of 2026-09-14 (33 agent
  passes, each report adversarially verified). The recon notes stay outside the repository because they cite local
  data; every number below was measured on the local production snapshot, the dev-box dump, or both.
- Tracking: none yet. File an issue per `docs/ISSUE-CONVENTIONS.md` after approval.
- Companion docs: `docs/neo4j-schema.md` (graph schema v1.0 as it is, and v1.1 as this design builds it) and the
  plan `docs/superpowers/plans/2026-09-14-graph-search-poc.md`.

## 1. Goal

Prove that sample search answered from Neo4j is faster and more capable than `advanced_search` at about 1.08M samples,
with the same visibility rules. The POC is step 1 of 3:

1. **This POC.** Put every sample's metadata in the graph, add people and projects, add a sample-type and attribute
   catalog, write graph schema v1.1, build a new endpoint `graph_search`, and benchmark it against `advanced_search`.
2. **Follow-up 1: Nessie** answers metadata questions from the graph (section 11.1).
3. **Follow-up 2: sync and CI.** Every writer keeps the graph current, with a weekly full sync and gates (sections
   11.2, 11.3).

Not the goal:
- Changing `advanced_search`. It stays untouched, and it is the baseline.
- Changing Nessie. Local Nessie is off while the POC graph is loaded (decision 7).
- Touching the dev box or production. Everything runs on the operator's workstation.
- Keeping the graph in sync after the benchmark. That is follow-up 2.

## 2. Why: what the recon found

| Fact | Consequence |
|---|---|
| `advanced_search` never reads `sample_auth_lookup`. A non-superuser is scoped by `EXISTS projects_samples` with a project list fetched from SEEK REST on every request (0.36 to 2.07 s). A superuser is unscoped | graph_search replaces a membership check plus a REST call, not auth_lookup |
| Every search is a full scan of `samples` (no index on `sample_type_id` or `uuid`), text is `json_metadata LIKE '%term%'` on a TEXT column, no SQL `LIMIT` or `ORDER BY` | the slowness is the query shape |
| Every match is materialized and copied about six times in the worker (about 21.5 KB of RSS per matched row) before one page is sliced out | the dev box OOMs are a paging problem; graph_search must page in the database |
| Membership is `group_memberships` joined to `work_groups`. Investigation is not project (16 investigations over 11 projects locally; TCGA is an investigation on the dev box) | the graph gets `Person -MEMBER_OF-> Project` |
| Walking Sample, Study, Investigation for scope changes the visible set for 102 of 107 people and hides every project-6 sample | each Sample carries its `projects_samples` projects |
| The graph holds no metadata locally (18 Sample keys) and raw metadata on the dev box (720 keys, written by batch upload's `s += row.properties`) | the writer is new; batch upload's projection is replaced in follow-up 2 |
| Neo4j Community 2026.07.1 allows uniqueness constraints, range, text, fulltext and composite indexes, and dynamic labels under `CYPHER 25`; it rejects existence and type constraints; a range-indexed value over about 8 KB fails the whole write | the writer and CI enforce what the database cannot |
| Spike at 1.07M replicated samples: attribute filters 14 to 456 ms warm; `x IN s.project_ids` adds 0.19x to 2.64x of admin time; EAV was 12x slower on multi-attribute AND | properties on the sample, a list property for scope |
| Nessie's entity agent resolves no attribute keys, and its graph context is a `keys(n) LIMIT 200` scan | a catalog in the graph is the foundation for follow-up 1 |

## 3. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Visibility | Match `advanced_search`: a non-superuser sees samples whose `projects_samples` projects intersect the projects they belong to through `group_memberships` joined to `work_groups` (former members included, as SEEK REST `Person#projects` does today). A Django `is_superuser` caller sees everything, including the 435 samples with no project | parity with the endpoint being replaced; the ISA walk is wrong for 102 of 107 people |
| 2 | Where membership is read | Per request from MySQL (the source of truth), passed to Cypher as a parameter. The graph also stores `Person -MEMBER_OF-> Project` for person-first queries and Nessie | nothing to go stale on the request path; no SEEK REST call |
| 3 | Sample-to-project link | Both `s.project_ids` (sorted distinct list) and `(:Sample)-[:IN_PROJECT]->(:Project)`, from the distinct `projects_samples` pairs | the list filters nodes already fetched; the edges serve "everything this person can see" |
| 4 | Metadata model | Every non-empty attribute of a sample is a property on that sample's node, named exactly by the attribute title. Empty values are not stored. Each Sample gets a per-type label `:T_<code>` and a complete `OF_TYPE` edge. No JSON string on the node; `json_metadata` is hydrated from MySQL for the returned page | uniform for people and Nessie; properties are per node and sparse, so a D.SEQ node never carries `Species` |
| 5 | Catalog | `(:SampleType)-[:HAS_ATTRIBUTE]->(:Attribute)` synced from SEEK and the dmac context tables, plus one `(:GraphMeta)`. Structure and admin-level `sample_count` only; per-project statistics and the `sensitive` flag are follow-up 1 | the source Nessie's generated schema reads; per-project stats would leak values across projects if done carelessly |
| 6 | Matching | `extensions` filters (new) are exact and case-sensitive. The fields shared with `advanced_search` keep its matching, which is case-insensitive, because the fulltext index is case-insensitive at no extra cost and id-set parity needs it | the operator does not need case folding; parity needs the old rule on old fields |
| 7 | Nessie during the POC | Local Nessie is off while the v1.1 graph is loaded into the live local stack; the stage 1 backup is restored afterwards | its schema fetch breaks on a metadata graph and its graph tool is unscoped |
| 8 | Data | Local production as the base plus TCGA only, in a new project (strategy C), built in a throwaway MySQL | TCGA separates cleanly; a separate project makes gating testable |
| 9 | Venue | Throwaway, memory-capped containers for the merge, the graph and the parity checks; the operator loads the result into the live local stack for the endpoint benchmark | the live stack holds production data and the host has little free memory |
| 10 | Benchmark arms | `advanced_search` (A), `graph_search` (G), and a SQL control (S): `advanced_search`'s SQL with an index on `sample_type_id`, `ORDER BY id` and `LIMIT` in the database | S separates the gain from paging from the gain from the graph |
| 11 | Context source | Today's dmac context tables, with `has_context` per SampleType; switch when the context generator lands | graph_search does not need the tiers; nothing blocks |
| 12 | Where docs live | Spec, plan and runbook in the repository on `feat/graph-search`; local paths are variables (`$GS_WORK`) | operator's choice; the repository is public |

Technical defaults (the recon's recommendations, adopted unless the operator objects):

- SampleType MERGEs on `sample_types.id`; `title` and `label` are unique too. The first sync sets `id` on the 49
  id-less nodes by title and creates the 11 missing types.
- Attribute MERGEs on `key` = `"<sample_type_id>:<title>"` (unique); declared attributes also carry `id` =
  `sample_attributes.id` (unique). The catalog is built after the merge assigns ids.
- The 40 undeclared observed keys become `declared: false` Attribute nodes (no `id`). Declaring them in SEEK goes on
  the curation list.
- Sample MERGEs on `id` (SEEK `samples.id`), unique. `uuid` gets a range index but no uniqueness constraint until the
  14 byte-exact duplicate uuids in MySQL are curated.
- The metadata key `UID` is never written (it equals `uuid`). `Type` and `ID` are written verbatim beside the system
  properties `type` and `id`.
- Casts per `value_type` (from SEEK's base type): Float and Integer where the value parses, Date to a Neo4j `date`
  where it parses (ISO date, ISO datetime, M/D/YYYY). A value that fails keeps its raw string and is counted. Bare
  years stay strings.
- Index budget: every (type, key) pair whose `value_type` is numeric or date and has values, every string pair with
  at least 1,000 samples and no value over 4,000 characters, and every benchmark filter key. Never index a lineage
  or file key. Everything else is reached through the `search_text` fulltext index or a label scan.
- The 79 ghost Sample nodes (a duplicate id, not in MySQL) are deleted. The 270 other graph-only Sample ids (349
  nodes) are relabeled `:OrphanSample` (label `:Sample` removed), keeping properties and edges.
- `CHILD_OF` is archived to a file (all 742,534 pairs, the 881 undeclared ones marked) and then deleted.
- Curated parents and children from the context table are stored as a delimited string in the POC.
- Label rule: `T_` + the title with every character outside `[A-Za-z0-9_]` replaced by `_`. Zero collisions across
  the 118 merged titles; the writer asserts that on every run.

## 4. Data: the merged dataset (gate M)

Built by scripts in the plan (task M1) inside the throwaway MySQL that already holds the dev-box dump. Source of the
remap list: the recon's merge analysis.

- **Base:** the local production backup (166,235 samples, 13 projects) loaded into a `merged` schema pair.
- **Added:** TCGA from the dev dump: 918,519 samples, 33 studies, 529 assays, one investigation, into a **new
  project** (next local id, 16) with one work group.
- **Kept ids:** TCGA sample ids (389,935 to 1,308,453), their policy ids and their `assay_assets` ids sit above every
  local id.
- **Remapped:** the investigation, studies, assays, permissions (let AUTO_INCREMENT assign), sample types (11 by title
  to local ids, 3 new: A.MET, A.RPPA, D.ARR), sample attributes (344 added to existing and new types, with
  `sample_attribute_type_id` mapped by title), `dmac.assays_internal_assays`, `dmac.internal_assays` (6 new titles),
  `dmac.sample_types_clades` (3 rows).
- **People:** TCGA's contributor maps to the seed test person `demo` (person 145), never to a real person by email.
  Two accounts are added for gating tests: a new non-superuser `tcgamember` (person, SEEK user and Django user, the
  same known password as the seed `user`), member of the TCGA project; the seed `user` (person 144, project 2) is the
  non-member control.
- **Not carried:** the 64,412 `-PUB` copies, test investigations, dev-only accounts, sessions, tokens, activity logs,
  the auth-lookup queue, the dev `settings` table.
- **Auth rows:** none in the throwaway. TCGA `sample_auth_lookup` rows are synthesized by SQL only when the operator
  loads the data into the live stack (step 5), so SEEK stays consistent.

Gate M (all automated, one report):
1. Per-table counts equal the expected merged counts (samples 1,084,754; projects_samples 1,127,894; assay_assets
   1,546,204), and every pre-existing local row is unchanged (counts plus a checksum per table).
2. No orphans on any remapped foreign key.
3. TCGA content intact: `SUM(LENGTH(json_metadata))` = 896,626,185 and per-sample CRC32 equal to the dev dump.
4. Every TCGA `json_metadata` key is a declared attribute title of its mapped type.
5. No four-byte character in any TCGA text written into a utf8mb3 column.
6. `samples.uuid` duplicates: only the 14 pre-existing local ones.

## 5. Graph schema v1.1

Specified in `docs/neo4j-schema.md` section "v1.1". In short:

- `(:Sample:T_<code> {id, uuid, type, title, project_ids, search_text, synced_at, <every non-empty attribute>})`
- `(:SampleType {id, title, label, ...})-[:HAS_ATTRIBUTE]->(:Attribute {key, id, title, value_type, declared, ...})`
- `(:Sample)-[:OF_TYPE]->(:SampleType)` for every sample
- `(:Sample)-[:IN_PROJECT]->(:Project {id, title})<-[:MEMBER_OF {has_left}]-(:Person {id})`
- `(:Investigation)-[:IN_PROJECT]->(:Project)`; `(:Sample)-[:DERIVED_FROM]->(:Sample)` child to parent;
  `IN_STUDY`, `IN_INVESTIGATION` unchanged; one `(:GraphMeta {schema_version: "1.1", catalog_hash, synced_at})`
- Fulltext index `sample_search_text` on `Sample.search_text`: every non-empty metadata value of the sample, joined
  by a newline, values only.

## 6. The writer: `graph_sync` (gate G)

A new package, `nextseek_api/graph_sync/`, and one management command, `graph_sync`. It reads MySQL and writes Neo4j;
it is the module follow-up 2 schedules, so it is built once, here.

- **Inputs:** `seek_production` (samples, sample_types, sample_attributes, sample_attribute_types, projects,
  projects_samples, group_memberships, work_groups, investigations, investigations_projects, studies, assays,
  assay_assets) and `dmac` (sample_types_context read through the ORM field `tags`, sample_attributes_unique,
  sample_types_clades, clades). Missing dmac tables mean `has_context = false`, never a failure.
- **Streaming:** samples are read by keyset (`WHERE id > :last ORDER BY id LIMIT :n`), parsed, projected and written in
  chunks (default 5,000 per transaction). Memory stays bounded by the chunk.
- **Replace, not merge:** `SET s = $props` replaces a node's whole property map, so a key deleted from MySQL leaves the
  node. Labels: the current `T_` label is set and any other `T_` label removed.
- **Order of a full run:** preflight (the label rule, duplicate checks, the ghost list) > delete ghosts > relabel
  orphans > archive and delete CHILD_OF > constraints > catalog (SampleType, Attribute, HAS_ATTRIBUTE) > Project,
  Person, MEMBER_OF, Investigation IN_PROJECT > samples (properties, label, OF_TYPE, IN_PROJECT) > lineage
  (DERIVED_FROM pairs declared by `collect_parent_tokens` and absent from the graph are created; declared edges keep
  their properties; a DERIVED_FROM edge between two `:Sample` nodes that MySQL does not declare is archived to a file
  and deleted, as CHILD_OF is. The first build found 9: 7 left stale by a later Parent edit, 1 self-loop, 1 to a
  uuid ending in a no-break space) > TCGA Study nodes (MERGE on `seek_study_id`) and IN_STUDY > the index budget >
  the fulltext index > GraphMeta.
- **Command:** `manage.py graph_sync --full | --catalog | --verify [--json] [--dry-run] [--chunk N]`. `--verify` is
  read-only and runs gate G. `--dry-run` projects without writing and prints counts.
- **Safety:** every write is chunked; `db.memory.transaction.max` is set on the throwaway Neo4j; the command refuses
  to run when `NEO4J_DATABASE["URI"]` is the live stack's unless `--i-mean-the-live-graph` is passed (the live load
  in step 5 is the operator's).

Gate G (`graph_sync --verify --json`, every check a number and a pass flag):
1. DERIVED_FROM pairs equal the MySQL-declared pairs among synced samples (0 missing; graph-only extras only on
   `:OrphanSample` nodes).
2. `project_ids` equal the distinct `projects_samples` pairs for every sample (sampled hash plus a full count per
   project).
3. Every property key on a `:T_X` node (system keys excluded) is the `title` of an Attribute on SampleType X.
4. OF_TYPE count equals the sample count; every Sample has exactly one `T_` label and it equals its SampleType's
   `label`.
5. Attribute nodes with an `id` equal `sample_attributes` rows by id; titles byte-exact.
6. For every person with a membership and the two test accounts, the graph-visible sample count equals the SQL
   `EXISTS projects_samples` count.
7. A metadata hash over 1,000 random samples equals the projection of their `json_metadata`.
8. Constraints and indexes are ONLINE; 0 label collisions; 0 SampleTypes without `id` or `label`.

## 7. The endpoint: `graph_search` (gate E)

`POST /nextseek_api/samples/graph_search/`, a new ViewSet in `nextseek_api/services/graph_search.py`, registered above
`samples` in `nextseek_api/urls.py` (the longest-prefix rule), declared in `ci/routes.py` in the same commit.

**Authentication:** `CsrfExemptSessionAuthentication`, `BasicAuthentication`; `IsAuthenticated`. No SEEK password is
needed, because scope is read from MySQL.

**Request:** the `advanced_search` body, byte for byte (`sampletype`, `filter_searchText`, `searchText_logic`,
`attribute`, `attribute_logic`, `filter_matchType`, `extra='forbid'`), the same `page` and `page_size` (default 100,
maximum 1,000), plus one optional field:

```json
"extensions": {
  "where": [
    {"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "Lung"},
    {"sample_type": "TIS", "attribute": "CellCount", "op": ">=", "value": 10000000}
  ],
  "lineage": {"direction": "descendant", "sample_type": "D.SEQ", "max_hops": 4}
}
```

`op` is one of `=`, `<>`, `<`, `<=`, `>`, `>=`, `IN`, `CONTAINS`, `STARTS WITH`. Every `where` item must name a
sample type and an attribute that exists on it in the catalog (422 otherwise). Items are ANDed. Values are cast by the
attribute's `value_type`; `CONTAINS` and `STARTS WITH` compare the stored value's text (`toString`), so a number held by
a string attribute matches by its digits, as advanced_search's Contain did. `lineage` keeps a sample only when a sample of that type lies within `max_hops` (1 to 4)
DERIVED_FROM hops in that direction; ancestors and descendants are not returned, so they need no scoping.

**Scope:**
1. `request.user.is_superuser`: no clause.
2. Otherwise: `users.login = request.user.username` gives `person_id`; `group_memberships` joined to `work_groups`
   gives the project ids. No person: 403 `Cannot determine project scope for this caller`. No projects: 200 with
   `total: 0` and no Cypher run.
3. The clause is `any(p IN s.project_ids WHERE p IN $projects)`, added by the query builder. Generated or
   user-supplied Cypher never supplies scope.

**Matching (fields shared with advanced_search):**

| Field | Rule |
|---|---|
| `sampletype` | title or id, OR across the list; unknown values dropped; no term and no resolvable type is 422 |
| text term (PARTIAL) | case-insensitive substring of any attribute value (key names do not count): fulltext candidates from `sample_search_text`, then `toLower(s.search_text) CONTAINS toLower($term)` |
| text term (EXACT) | case-insensitive equality with any attribute value, or with the named attributes (trimmed) when `attribute` is given, as advanced_search's two stages do |
| `attribute` | applies only with a term; each term must match any (`OR`) or every (`AND`) named attribute; names resolve case-insensitively against the catalog for the requested types (every case variant included) |
| `searchText_logic` | AND or OR across terms |
| UID terms | exact `uuid` match, unioned with the text results |

**Execution:** a pure query builder turns the validated request and scope into one Cypher statement for the page
(`ORDER BY s.id SKIP $skip LIMIT $limit`, returning ids) and one for `total` and `sampleTypes` (a count and the
distinct `type` values). Both run in `session.execute_read` with a 60 s timeout. Property names from the catalog are
backtick-quoted; every value is a parameter.

**Hydration:** the page ids are read from `seek_production` by primary key with the same columns advanced_search
returns (`id`, `title`, `sample_type_id`, `sample_type`, `uuid`, `contributor_id`, `first_name`, `created_at`,
`json_metadata`, `assays` from `assay_assets` and `assays`). Rows keep id-ascending order. `attributeValue` is `""`;
the HTML anchors are omitted.

**Response:** advanced_search's envelope (`total`, `rows`, `footer`, `sampleTypes`, `noSampleTypes`, `msg`, `status`)
validated by `SampleAdvancedSearchResult`. `?debug_meta=1` appends `{"debug": {"cypher_ms", "count_ms",
"hydrate_ms", "total_ms"}}` to `footer`.

**Declared differences from advanced_search** (excluded from parity):
1. Rows are in global `id` order; a mixed UID-plus-text search has `footer` and `sampleTypes` (advanced_search puts
   UID rows first and drops both).
2. PubMed syntax inside one string (parentheses, `NOT`, `term[TYPE]`) is not supported; the string is one term.
3. `sampleTypes` is computed after every filter.
4. An out-of-range page returns an empty page, not every row.
5. A caller with no SEEK person is 403 even when Basic credentials are present; Token authentication is not offered.
6. No highlight HTML.

Gate E:
1. Unit tests for the query builder (every filter shape, scope, escaping, catalog validation), the scope resolver and
   the view (401, 403, 422, happy path), plus the ViewSet conventions pair and `scripts/validate_viewset_conventions.py`.
2. Parity: for the benchmark query set and every scope (superuser, each distinct project set held by a real person,
   `tcgamember`, `user`), graph_search's id set and `total` equal advanced_search's rule on the same data, except the
   declared differences. The advanced_search side is computed by its own engine with `scoped_project_ids` supplied
   directly, and by an id-only SQL form for type-only shapes too broad to materialize.
3. A read-only check: a write statement sent through the endpoint's session is refused.

## 8. Loading into the live local stack (gate L, operator)

The operator runs this; the plan gives the runbook.

1. Turn local Nessie off for the window (the operator's switch; the plan names the options).
2. Recreate `nextseek` so the compose memory cap (`NEXTSEEK_MEMORY`, 16 GiB) applies.
3. Load the merged MySQL and synthesize TCGA `sample_auth_lookup` rows (about 114M rows, 15 to 22 GB; recon estimate).
4. Load the v1.1 graph into the stack's Neo4j (a dump from the throwaway), after checking free disk.
5. Deploy `feat/graph-search` with `./startup.sh rebuild`.

Gate L: the stack is healthy; both endpoints answer for `demo`, `tcgamember` and `user`; parity holds on the live
data for the query set; gate G's `--verify` passes against the live graph.

Rollback: the stage 1 backup's documented restore, then a rebuild on `origin/dev`.

## 9. The benchmark (gate B)

- **Arms:** A (`POST /nextseek_api/samples/advanced_search/`), G (`POST /nextseek_api/samples/graph_search/`), S (the
  captured advanced_search SQL with `ORDER BY A.id LIMIT 100` and an index on `samples.sample_type_id`, run directly
  on a throwaway copy of the merged MySQL).
- **Accounts:** `demo` (superuser), `tcgamember` (TCGA only), `user` (project 2, not TCGA). Every non-superuser run is
  paired with the same query as `demo`.
- **Queries:** re-picked on the merged data: narrow attribute equality; broad type-only (one type near 100k samples,
  and A.VCF at about 283k); crossed attributes; keyword across all types; the OR-retry shape Nessie sends; multi-type
  plus text; 50 UIDs; and graph-only shapes (paired filters, a numeric range, a lineage condition). Each shape
  declares where exact and case-insensitive matching differ.
- **Configurations:** as deployed (MySQL 128 MiB buffer pool, Neo4j 512 MiB page cache) and an equal-budget pair
  (both sized alike, recorded).
- **Protocol:** warm is 1 warm-up plus 5 runs (median and range); cold is the first run after restarting the database
  containers; one run with 4 concurrent clients.
- **Recorded per query and account:** HTTP status, `total`, the id-set difference against A, wall time through nginx,
  database time (`debug_meta` for G, captured SQL time for A), response bytes, and peak worker RSS growth measured in
  a capped process, never an uncapped live worker.
- **Pass criteria (operator to confirm):** parity outside the declared differences; G's p50 and p95 below A's on every
  shape; the two breaking shapes (keyword across all types, the OR retry) in seconds, not tens of seconds; G's peak
  RSS growth per request bounded by the page size whatever the match count.

After the benchmark: restore the stage 1 backup and turn Nessie back on, or keep the merged data if the operator
chooses.

## 10. Constraints

- The host has about 7 GB of free memory and its swap is full. Every throwaway container has `--memory`; broad
  advanced_search shapes run only in capped processes.
- No SSH to the dev box or production. No test on production.
- The seed dumps and the merged dumps contain real personal data: they stay outside the repository and are never
  uploaded. Tracked files carry no emails, names or personal paths.
- Never rebuild or recreate the live stack from an agent; the plan's operator steps are marked.
- Stage files by name. Conventional commits with module scopes. No em-dashes.

## 11. Follow-ups (scope only)

Each item carries enough context to mock it up; none is specified here.

### 11.1 Follow-up 1: Nessie on the metadata graph

Code under `NessieAI/chat_nextseek/src/chat_nextseek/`.
- A0. Context export reports success per source (`config.py::_ensure_context_files`, `_fetch_context_files_from_db`).
  Locally it fails with MySQL error 1045 (credential mismatch in the operator's env), so local Nessie runs on a stub.
- A1. Scope first: a server-supplied `{is_admin, project_ids}` on every graph path (`helpers/tools/neo4j.py`,
  `ns/granular.py`, `agents/planner/tools.py`, `reports/runners.py`, `nextseek_api/services/entity_tree.py`), fail
  closed, `execute_read`. Non-admins get graph_search and fixed scoped operations, not free Cypher.
- A2. Catalog reader replaces `_fetch_neo4j_schema` (`keys(n) LIMIT 200`) with live catalog reads cached on
  `GraphMeta.catalog_hash`; nothing is written into the package.
- A3. Renderer: structure plus a type index plus at most 3 resolved types at 25 attributes (about 30 KB, compact
  text) in `agents/graph.py`, `agents/system.py`, `agents/parser.py`.
- A4. Property guard per label from the catalog (`known_node_properties`).
- A5. Typed entities: additive `attributes[{sample_type, key, op, value}]` and `project_ids`, validated against the
  catalog; the 118 graph SampleTypes replace the committed type list (17 types are unreachable today).
- A6. Attribute filters route to graph_search; fold the 15 hard-coded `advanced_search` literals in 6 files into one
  check.
- A7. Prompts drop "no metadata on the node" and forbid whole-node returns.
- A8. Follow-up questions re-query the stored predicate when `total` exceeds the rows held.
- A9. A Container-CC `nextseek-graph-schema` operation replaces the baked schema files.
- A10. Corpus: rewrite the 56 criteria that assert advanced_search; add facet and attribute-conditioned variants.
- Moved here from the POC: per-project statistics (`(:Attribute)-[:USED_IN {sample_count, top_values,
  top_counts}]->(:Project)`, about 6,111 edges merged) and a curated `sensitive` flag.

### 11.2 Follow-up 2: keep the graph in sync

The engine is section 6's module.
- B2. Catalog rebuild in full each run (about 6,000 rows), per-type `catalog_hash`.
- B3. Per-project statistics recomputed for touched types, in full weekly.
- B4. dmac tables `graph_sync_outbox` and `graph_sync_run`; a leased `manage.py graph_sync --loop` started from
  `docker/scripts/entrypoint.sh` (Celery beat never runs): outbox every 5 s, catalog on change and nightly, sample
  delta nightly, **full sync weekly**, drift check nightly.
- B5. Hooks after commit: the native attribute API (`DjangoExecutionServices.record_commit`), the legacy attribute
  editor, `SampleTypeProxyViewSet`, `derive_sample_type_requirements`, the clade admin views, batch upload (its node
  projection replaced by section 6's), and the 18 sample writers the recon listed.
- B6. A reconciler for writes that bypass Django (the SEEK Rails UI, hand SQL, `refresh_samples`); the weekly full
  sync bounds staleness at 7 days.
- B7. Manage `idx_updated_id` and `idx_samples_sample_type_id` on rebuild (fixups run on install only today).
- B8. A superuser, read-only sync status endpoint.
- B9. Cleanups found on the way (`cladeSave` writes color from order; `syncSampleTypes` inserts NULL clades; retire
  the attribute editor that writes on GET).

### 11.3 CI/CD

- CI1. A blocking writer registry in `ci/gate`, modelled on `ci/routes.py`: every writer of samples, attributes,
  types or project links is listed with its sync hook.
- CI2. A drift gate after every rebuild on local and dev: `graph_sync --verify --json`, failing on drift or on a
  stale sync.
- CI3. Catalog coverage, report-only first.
- CI4. Nessie context gates: size budget, no email-like values, no stale labels.
- CI5. A write-lane hook test: create an attribute, poll until the outbox row is done and `catalog_hash` matches.
- CI6. graph_search in both `read_safe_endpoints.json` copies; a test that `execute_read` refuses writes.

## 12. Risks

- **"Faster" is unproven at the endpoint.** The spike compared the graph with a hand-written SQL proxy on replicated
  data, single-client, on a noisy host.
- **Memory.** The throwaway MySQL, the throwaway Neo4j and the parity harness compete with the live stack for about
  7 GB.
- **TCGA's shape differs from production's:** about 976 bytes of JSON per sample against 1,698, 276 keys, and only
  2.2% of samples carry the full key set. The index budget and the spike's numbers must be re-derived on the merged
  census.
- **Text semantics.** Lucene tokens are not SQL substrings; the candidate-then-verify rule restores substring
  semantics, and parity decides.
- **The dev box already carries raw metadata** on its graph, and Nessie there is unscoped (everyone is in one
  project). A1 closes it.
- **Keys.** 14 duplicate uuids in MySQL; `Sample.uuid` stays non-unique until curated.
