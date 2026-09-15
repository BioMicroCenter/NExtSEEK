# graph_search follow-up 2: every writer keeps graph 2.0 in sync (discovery spec)

- Date: 2026-09-15
- Branch: `feat/graph-search-sync`, cut from `feat/graph-search` at `4b3e087a`, merged with `aa9706d9`
- Status: discovery spec, revised the same day for the operator: refocused on wiring the current writers, and every
  question decided (section 18). Nothing here is built. The POC already ships `manage.py graph_sync --full`,
  `--catalog` and `--verify`; the two dmac tables exist only as an uncommitted draft model and migration, which the
  plan's first task starts from.
- Tracking: none yet. File an issue per `docs/ISSUE-CONVENTIONS.md` after approval.
- Companion: `docs/superpowers/specs/2026-09-15-graph-search-sync-inventory.md` (the writer catalogue `WR-01` to
  `WR-28`, both inventories, the cross-check and the recon corrections). Plan, in four Workflow runs:
  `docs/superpowers/plans/2026-09-15-graph-search-sync.md`.
- Parent design: `docs/superpowers/specs/2026-09-14-graph-search-poc-design.md` (section 6 is the writer this work
  wires in). Graph schema: `docs/neo4j-schema.md` "v1.1" ("graph 2.0" below).
- This supersedes the first draft of the same file (commit `9c082ac7`).

## 1. Goal

The operator's goal: **wire up NExtSEEK to automatically keep the graph up to date using the current functions, and
then build everything CI/CD needs to check it: the sync, and the new features as well.**

Concretely:
1. Every function in NExtSEEK that writes a table the graph reads calls graph_sync after it writes (a hook), so the
   graph follows the write within seconds. Batch upload's graph stage becomes graph_sync's.
2. What no NExtSEEK function sees (the SEEK Rails UI and REST API, Rails jobs, hand SQL, operator scripts) is caught
   by a nightly targeted sync, and a weekly full sync is the backstop.
3. CI proves it: a registry that fails when a writer or a route has no hook, a drift check after every rebuild,
   hook tests, and CI for what the POC already built (graph_search, graph_sync) that runs nothing today.

The engine is the POC's `nextseek_api/graph_sync/`, reused, not redesigned: it gains by-id entry points, the
labels it does not write yet, a loop and a few properties it needs to find changes (section 6). Nessie is
follow-up 1's.

### 1.1 The key finding: TCGA's assay labels are missing from the local graph

- **On the dev box, all 1,213,093 TCGA DERIVED_FROM edges carry assay labels** (`assay_id`, `internal_assay_id`,
  `internal_assay_title`, `internal_assay_ids`, `internal_assay_titles`, for example "Patient Visit"), and none
  carries a protocol label. Measured 2026-09-15 by streaming the dev-box graph dump: 1,213,093 edges between TCGA
  samples (sample ids 389,935 to 1,308,453), each of the five keys present on all of them.
- **The POC did not copy those edges.** It rebuilt lineage from MySQL's parent tokens instead of copying the dev graph
  (whose Sample nodes carried 720 raw metadata keys), and graph_sync's `WRITE_MISSING_LINEAGE` sets only `child_id`
  and `parent_id` on an edge it creates. So in the local graph the TCGA edges have no assay labels. The 802k
  production edges kept theirs, because a declared edge that already exists keeps its properties.
- **The data to recompute them was merged.** Gate M: TCGA's internal-assay links 529 of 529, and
  `dmac.assays_internal_assays` 970 rows (441 local plus 529) with 0 orphans; the merge maps internal assays by
  title and records every renumbering in `dmac.gs_remap`.
- **So the label step is not new scope.** It replaces batch upload's label computation, which this rewiring removes
  from batch upload, and it fills this gap. It is verified against the dev box's TCGA labels and the local
  production labels before it writes any live graph (section 17).

## 2. Two facts the first draft got wrong

1. **Nothing of increment 1 is built.** Only the POC's one-time `graph_sync --full` (with `--catalog` and
   `--verify`) exists. The two tables are an untested model, a migration and a re-export line.
2. **CI never calls graph_search.** The route registry accepts it (`ci/routes.py`) and the API-root test lists it
   (`ci/smoke/test_health.py`), but nothing sends it a POST: the T0 sweep keeps only routes whose methods include
   GET (`ci/smoke/test_reachability.py::_callable_routes`), so the POST-only entry is dropped at collection with no
   skip line. It is in neither `read_safe_endpoints.json` copy nor in `_READ_POST_PATHS`.

## 3. The operator's decisions this spec builds on

| # | Decision |
|---|---|
| D1 | **Mechanism.** A hook in every NExtSEEK writer, enforced by the CI writer registry; a **nightly targeted sync** for the writes hooks cannot see; a **weekly full sync** as the backstop. Not a nightly full sync |
| D2 | **Rejected: database-level capture.** MySQL triggers or binlog change capture would put the sync inside SEEK's schema and Rails' transactions (triggers on tables NExtSEEK does not own, invisible to CI), or add a new service with replication privileges and binlog retention, and would need a second projection path |
| D3 | **Check everything from both ends**: the routes of `ci/routes.py` traced to the tables they write, and a scan of the code for every write; anything found by only one side is a gap (companion section 5) |
| D4 | **Batch upload's graph writer changes**: it produces exactly what graph_sync produces, on create, update and delete, through graph_sync's projection and writer, never a second projection. `s += row.properties` goes. One deletion rule for every path |

The rest is decided in section 18.

## 4. How changes are found and applied: the three layers

| Layer | When | Sees | Cost at 1.08M samples |
|---|---|---|---|
| **Hooks** | after every write made by NExtSEEK code (16 writers, companion WR-01 to WR-16) | exactly what that writer changed | per write: an outbox row; the drain applies it within seconds |
| **Nightly targeted sync** (`graph_sync --reconcile`) | 02:00 UTC | every row whose content differs from what its node was written from, however it changed (section 10) | about 1 to 3 minutes of reading, estimated, plus writes for what changed |
| **Weekly full sync** (`graph_sync --full`) | Sunday 03:00 UTC | everything, including what only the graph holds and the statistics | about 8.5 minutes measured in the POC's lane |

A hook never calls Neo4j in a request: it writes one row to `graph_sync_outbox` after the writer's own commit and
returns; the loop drains the row through the same functions the nightly and weekly syncs use (section 7). Batch
upload also syncs inline (section 8).

## 5. The core table: every graph 2.0 element, its sources, its writers, how it stays in sync, what proves it

Writers are the companion's ids. "Reached by": R = a route or page in `ci/routes.py`, C = a Celery task or job
runner, M = a management command, S = install or a script, X = Rails or hand SQL outside NExtSEEK's code. Sync: H =
a hook at the named site, N = the nightly targeted sync, F = the weekly full sync. Every row is also covered by the
writer registry gate (section 14, CI-1), which fails when a writer of its sources is not listed with its hook.

| # | Element | MySQL sources | Writers of those sources, and how they are reached | Operations | How it stays in sync | Proof in CI |
|---|---|---|---|---|---|---|
| E1 | `Sample` node and its system properties `id`, `uuid`, `title`, `type`, `synced_at`, `source_hash` (new, section 6) | `samples.id`, `uuid`, `title`, `sample_type_id`; `sample_types.title` | WR-01, WR-02 (R `batch-upload/start` then C `batch_upload.run`); WR-07 (R sample proxy); WR-12 (R `/seek/sampleupload/`); WR-13 (R `/seek/samples/delete/`); WR-22, WR-23 (X); WR-18 (S) | create, update, delete | H: stage 5 enqueue and stage 6 inline sync (WR-01, WR-02); after a 2xx in `SampleProxyViewSet.create`, `.partial_update`, `.destroy` (WR-07); after `_storeSample` and the update paths (WR-12); after `_deleteOneSample` commits (WR-13). N for WR-22, WR-23. F | drift `samples.missing_in_graph`, `samples.not_in_mysql`, `samples.source_hash_mismatch` (CI-4); gate G check 4; hook tests per site (CI-6); write-lane E2E (CI-7) |
| E2 | Sample metadata properties (every non-empty attribute, typed by `value_type`) | `samples.json_metadata`; `sample_attributes` and `sample_attribute_types.base_type` (the casts) | WR-01, WR-02, WR-04 (C `batch_upload.resolve_orphans`), WR-05 (R attribute API, C `attribute_mutations.run`; **no `updated_at` bump**), WR-06 (R legacy attribute editor), WR-07, WR-12, WR-16 (M `backfill_publication_attributes`; no bump), WR-22, WR-23 (no bump); casts change with WR-05, WR-06, WR-08, WR-20 | update (and create with E1) | H: as E1, plus orphan resolution after its commit (WR-04), `DjangoExecutionServices.record_commit` enqueues `samples_of_type` (WR-05), the end of the two legacy editor views (WR-06), the backfill command's end (WR-16). N: the source hash covers the row bytes and the type's value types. F | gate G check 7 (sampled metadata hash) and drift `samples.source_hash_mismatch` over every sample (CI-4) |
| E3 | `search_text` (every non-empty value, the UID's too) | `samples.json_metadata` | as E2 | update | as E2 (the projection writes it) | gate G check 7; graph_search smoke (CI-3) |
| E4 | Type label `T_<code>` and `OF_TYPE` | `samples.sample_type_id`; `sample_types.title` | a sample's type: WR-07, WR-22; a type's title: WR-08 (R sample-type proxy), WR-22 | update | H: after a 2xx in `SampleTypeProxyViewSet.create`, `.partial_update`, enqueue `catalog` and `samples_of_type` (a rename relabels every sample of the type). N: the source hash includes the type title. F | gate G check 4 (one `T_` label per sample, equal to its SampleType's `label`; OF_TYPE count); new gate G check: no node with a `T_` label lacks `:Sample` (CI-9) |
| E5 | `IN_PROJECT` edges and `project_ids` | `projects_samples` (no timestamp, no primary key, no index leading with `sample_id`); `projects` | WR-01, WR-02, WR-07, WR-12 (`_updateSampleProject`), WR-13 (delete), WR-22 (project admin, sharing), WR-18 | create, delete | H: as E1. N: the source hash includes the sample's sorted project ids. F | gate G checks 2 and 6; drift `2.scope.*` (CI-4) |
| E6 | `parent_titles`, `parent_title_hashes` (graph-only lists; orphan discovery reads them) | derived: each parent token of `samples.json_metadata`, a UID token resolved to the parent's identity in its stored metadata | the E2 writers; today computed only by batch upload's stage 6 (`neo4j_sync.py::enrich_parent_titles`) and WR-17 | update | projection-owned (section 18): written by every graph_sync path with the node. H, N, F as E2 | projection unit tests; a gate G check over the sampled nodes (CI-9) |
| E7 | `DERIVED_FROM` between Sample nodes (which pairs exist) | parent tokens of the child's `samples.json_metadata` (`collect_parent_tokens`, UIDs only), resolved through `samples.uuid` | the E2 writers (the child side); any create, delete or uuid change of the parent (E1 writers); graph writers today: stage 6, orphan resolution, the legacy upload, graph_sync | create, delete | H: `sync_samples` writes the declared pairs of its samples as children and archives and deletes their undeclared ones. N: a changed child is re-synced; a new uuid triggers a bounded scan for old children that name it (section 10). F: the full lineage rule | gate G check 1 (declared pairs equal the graph's, lane and live) |
| E8 | DERIVED_FROM assay labels `assay_id`, `internal_assay_id`, `internal_assay_title`, `internal_assay_ids`, `internal_assay_titles` | `assay_assets` (Sample rows) of both ends; `assays` (id, title: the fallback); `dmac.assays_internal_assays`, `dmac.internal_assays` | assay links: WR-01, WR-02, WR-07, WR-11 (R `assay-registrations`, C job runner), WR-12, WR-13, WR-22; `assays`: WR-09 (R assay proxy), WR-22; the map: WR-15 (R internal-assay admin); graph-only today: stage 6, WR-11's recompute, orphan resolution, WR-12, WR-24 (curation relabel) | update | **no owner in graph_sync today, and missing on every TCGA edge of the local graph (section 1.1).** New: the label step (section 7.3). H: the sample hooks relabel both directions of the touched samples' edges; the internal-assay admin views and the assay proxy enqueue `assay_map`. N: the source hash includes the sample's sorted assay ids; a hash of the resolved map on GraphMeta catches map changes. F: every edge against the rule | the label verification (plan task V1: TCGA against the dev box, production against the local graph); new gate G check 9 `lineage.labels` and its drift twin (CI-9, CI-4) |
| E9 | DERIVED_FROM protocol labels `protocol_id`, `protocol_title` | the child's `json_metadata.Protocol` (the house three-format rule in `nextseek_api/batch_upload/helpers.py`); `sops` (id, title) | the E2 writers; `sops`: WR-09 (R SOP proxy), WR-22; graph-only today: an upload sheet's `sop_id` (stage 6) | update | as E8, with `protocol_map` enqueued by the SOP proxy hook | as E8 |
| E10 | `SampleType` (id, title, label, uuid, description, deprecated, context fields, clade, counts) | `sample_types`; `dmac.sample_types_context`; `dmac.sample_types_clades` joined to `clades` | WR-08, WR-14 (R clade admin), WR-21 (hand edits; the coming context apply step), WR-22, WR-18, WR-19 | create, update, delete | H: the sample-type proxy and the clade admin views enqueue `catalog`. N: the whole catalog (about 6,000 rows) is rebuilt nightly. F | gate G check 8; drift `drift.catalog.sample_types`, `drift.catalog.hash` (CI-4) |
| E11 | `Attribute` and `HAS_ATTRIBUTE` (declared and `declared: false`, `sample_count`) | `sample_attributes`; `sample_attribute_types`; `dmac.sample_attributes_unique` (meanings); undeclared keys observed in `samples.json_metadata` | WR-05, WR-06, WR-08, WR-20 (hand SQL: descriptions, meanings, the publication attributes), WR-22, WR-19 | create, update, delete | H: `record_commit` (WR-05), the legacy editor views (WR-06), the sample-type proxy (WR-08) enqueue `catalog`; a sample sync adds a `declared: false` Attribute for a key its type does not declare. N: catalog rebuild. F: `sample_count` | gate G checks 3 and 5; drift `drift.catalog.types_with_attribute_set_diff` (CI-4) |
| E12 | `Project` (id, title) | `projects` | WR-09 (R project proxy), WR-22, WR-18 | create, update | H: the project proxy enqueues `isa`. N: a full rewrite of the ISA nodes (tens of rows). F | new drift `drift.isa.projects` (CI-4); gate G check 2 |
| E13 | `Person` and `MEMBER_OF {has_left, time_left_at}` | `group_memberships` joined to `work_groups` | WR-10 (R users API, through the Rails runner), WR-22 (project admin, join requests), WR-18 | create, update | H: the users API enqueues `membership`. N: a full rewrite (about 170 rows). F | gate G check 6 (graph-visible counts per membership scope) |
| E14 | `Investigation`, `Investigation.project_id`, `(:Investigation)-[:IN_PROJECT]->(:Project)` | `investigations`; `investigations_projects` | WR-09 (R investigation proxy), WR-22, WR-18 | create, update | H: the investigation proxy enqueues `isa`. N: full rewrite. F | new drift `drift.isa.investigations` (CI-4) |
| E15 | SEEK `Study` nodes (keyed on `seek_study_id`) and `IN_STUDY`; paper-level Study nodes | `studies` (id, title, investigation_id); `assays.study_id`; `assay_assets` Sample rows. Paper-level studies have no MySQL source | studies: WR-09 (R study proxy), WR-22; the links: the E8 assay-link writers; graph today: stage 6 keys Study on the SEEK id, which collides with v1.1 (companion section 6, item 1) | create, update | H: the study and assay proxies enqueue `isa`; the sample syncs write `IN_STUDY` for their samples. N: the source hash includes assay ids; the studies table is diffed nightly. F. Paper-level Study nodes are left as they are | new drift `drift.isa.studies` (CI-4) |
| E16 | `GraphMeta` (`schema_version`, `catalog_hash`, `label_maps_hash` new, `synced_at`) | derived | graph_sync only (WR-26) | update | every graph_sync run | gate G check 8; the status endpoint (CI-5) |
| E17 | A sample that left MySQL (the deletion rule, section 9) | the absence of the `samples` row | WR-07 destroy, WR-13, WR-22 | delete | H: the proxy destroy and the legacy delete enqueue `retire`. N: an id on a node but not in MySQL is retired. F | drift `samples.not_in_mysql`; new gate G check: no node carrying a `T_` label lacks `:Sample` (CI-9) |

Summary: 17 elements; 21 source tables (15 SEEK, 6 dmac) plus two derived elements (E6, E16); 28 writer entries, of
which 16 have a hook site, 8 are seen only by the nightly targeted sync, 2 write the graph alone and are repaired
only by the weekly full sync, 1 is the owner, 1 inherits other writers' hooks and 1 is dead (companion section 2).

## 6. What graph 2.0 gains for the sync itself (schema 1.2)

`docs/neo4j-schema.md`'s versioning rule bumps the version with any change, so these ship as schema 1.2 in one
commit with the writer's `SCHEMA_VERSION`:
- **`Sample.source_hash`**, a system property: a digest of everything the node is projected from (section 10.3).
- **`parent_titles` and `parent_title_hashes`**, projection-owned (today graph_sync only preserves what batch upload
  wrote).
- **DERIVED_FROM labels** written by graph_sync (section 7.3); the legacy `assay_title` property goes.
- **`GraphMeta.label_maps_hash`**: a digest of the resolved assay map and of `sops` (id, title).
- **The deletion rule** of section 9.

The loop refuses to write a graph that is not at the writer's version, so a 1.1 graph waits for the operator's first
`graph_sync --full` at 1.2 (section 17).

## 7. One code path: what graph_sync gains

Everything that writes the graph goes through `nextseek_api/graph_sync/`. The existing interfaces stay:
`run.full_sync`, `run.catalog_sync`, `verify.gate_g` and the command's existing flags keep their signatures and
meaning.

### 7.1 The by-id entry points (`graph_sync/targeted.py`, new)

| Function | Does | Called by |
|---|---|---|
| `sync_samples(driver, db, ids, *, run_dir)` | refuses unless `GraphMeta.schema_version` is the writer's; takes the graph-write lock (7.4); reads those rows by id, their projects, assay ids and parent tokens; builds the catalog, running `catalog_sync` first when a row's type has no SampleType node; projects each row (`projection.project_sample`, with `source_hash` and the parent lists); `writer.write_samples`; the declared lineage of these samples as children (create missing, archive and delete undeclared); the labels of every edge incident to them, both directions (7.3); `IN_STUDY` for them; `declared: false` Attribute nodes for keys their type does not declare; `SampleType.sample_count`; an id MySQL no longer returns is retired (section 9). Returns a counts dict | the drain (`samples`), batch upload stage 6, `neo4j_only`, the nightly targeted sync, `graph_sync --samples` |
| `sync_samples_of_type(driver, db, type_id)` | streams the type's ids and calls `sync_samples` in chunks | the drain (`samples_of_type`) |
| `retire_samples(driver, db, ids, *, run_dir)` | the deletion rule (section 9) | the drain (`retire`), `sync_samples`, the nightly and weekly syncs |
| `relabel_for_maps(driver, db)` | recomputes the resolved assay map and `sops`, compares them with `GraphMeta.label_maps_hash`, and relabels the edges between members of the changed assays and the edges whose child's protocol resolution changed | the drain (`assay_map`, `protocol_map`), the nightly targeted sync |
| `sync_small_tables(driver, db)` | `write_projects`, `write_investigation_projects`, `write_people_and_memberships`, SEEK Study titles | the drain (`isa`, `membership`), the nightly targeted sync |

### 7.2 New readers (`graph_sync/sources.py`)

`samples_by_ids`, `sample_projects_for(ids)`, `sample_assay_ids_for(ids)`, `uuid_to_ids_for(tokens)`,
`seek_study_links_for(ids)`, `resolved_assay_map()`, `sops_map()`, and the ordered keyset stream the nightly
targeted sync reads (section 10). Every reader keeps the module's rules: bound parameters, byte-exact titles compared
in Python, soft on missing dmac tables.

### 7.3 The DERIVED_FROM label step (`graph_sync/labels.py`, new)

Batch upload's rule, moved into graph_sync and fed from MySQL:
`nextseek_api/batch_upload/neo4j_sync.py::build_derived_from_payloads_from_db` Steps 1 to 3 (the only writer that
sets all nine properties coherently, and the one the curation relabel already mirrors): the assays both endpoints
share in `assay_assets`, resolved through `dmac.assays_internal_assays` to `dmac.internal_assays` with the smallest
internal id winning and the SEEK assay as the fallback; the protocol from the child's stored `Protocol` through
`helpers.parse_protocol_value` and `lookup_sop_ids_by_title`. Every property is set explicitly, nulls and empty lists
included; `assay_title`, which only the legacy upload writes, is removed.

**The labels graph_sync computes must equal what batch upload computes today from MySQL.** Where an edge's stored
label differs (a label an upload sheet supplied and MySQL never stored, a label left stale by an internal-assay
rename), the difference is reported, never changed silently: by the verification before any live write (plan task
V1), and by the per-property change counts every full sync reports.

Called three ways: over every edge by the full sync (setting only the edges that differ), over the edges incident to
touched samples (both directions), and over the members of changed assays or protocols (`relabel_for_maps`). It
replaces `assay_registration/graph.py::recompute_for_samples`, the label half of stage 6, orphan resolution's label
SET, the legacy upload's `getConnectingRelationships` and `backfill_shared_assays.py`.

### 7.4 The graph-write lock

Nothing serializes graph writers today: a sample written to the graph between the full sync's MySQL id scan and its
graph read is classified graph-only. Every graph_sync write unit (a full sync, a catalog sync, the nightly targeted
sync, one drained outbox row, batch upload's inline sync) takes one lock: MySQL
`GET_LOCK('nextseek_graph_write', timeout)` on the dmac connection, released at the end or when the session dies. A
full sync holds it for its whole run; the drain and the inline sync wait at most a bounded time and otherwise leave
their row pending. On SQLite (the unit-test lane) the lock is a no-op behind the same function.

## 8. Batch upload

**What changes.** `neo4j_sync.py::upload_all` and its v1.0 writers go (companion section 7). Stage 5, inside each
batch's transaction, inserts one outbox row (`samples`, key `batch:<job id>:<batch>`, the batch's committed ids in
`payload`). The insert rides the batch's own connection, which already reads dmac by qualified name; if the seek
user lacks INSERT on `dmac.graph_sync_outbox`, the row is written right after the batch commits instead (the plan's
task checks the grant). Stage 6 calls `targeted.sync_samples(ids)` for every id that has a `sample_id` in the
outcomes (inserted, updated and skipped-duplicate rows alike), under the graph-write lock with a 60 s wait, marks the
rows done on success, and reports `graph: synced (N)` or `graph: pending (N)` in the job's totals and summary CSV.
`neo4j_only` becomes `sync_samples` of the ids the sheet's UIDs resolve to. Orphan resolution keeps its MySQL
rewrite, writes nothing to the graph inside its transaction, and enqueues the resolved children after the commit.

**Inline, with the outbox as the record:**
1. A user expects an upload to be searchable when the job says SUCCESS. Today even a stage 6 that succeeds does not
   make new samples findable by graph_search (no `T_` label, `project_ids` or `search_text`).
2. The drain is one worker and waits behind the weekly full sync (about 8.5 minutes); the inline sync does not.
3. The transactional outbox row survives what the inline sync cannot: a cancel or soft time limit between stages 5
   and 6, a worker crash, a Neo4j outage, a graph not yet at the writer's version.
4. The lock keeps the inline sync from racing the full sync; the outbox makes a lost race a delay, not a loss.
5. The cost lands in the job: at the POC lane's measured rate (1.08M samples in 184.5 s of sample writes) a
   100,000-sample upload adds about 20 s, estimated.

**Deletion.** Batch upload deletes no sample (`nextseek_api/batch_delete/` holds models and tests only). Its update
path deletes `assay_assets` rows, which the label step reflects through the sample sync.

## 9. The deletion rule

Today one deleted sample can end in three states: gone (the legacy delete), still a live `:Sample` (the SEEK proxy
destroy and every Rails delete, until a full sync), or an `:OrphanSample` after a later full sync. `RELABEL_ORPHANS`
keeps every `T_` label. That is **latent, not live**: `relabel_orphans` runs before the samples step and graph-only
ids are never projected, so today's orphans carry no `T_` label and graph_search's results are unaffected. It would
bite only after a synced sample is deleted from MySQL and a later full sync relabels it; graph_search's attribute and
lineage queries start from `MATCH (s:T_X)` without `:Sample`, so that node would still match. The rule below removes
it.

**The rule, applied by one function (`targeted.retire_samples`, through `writer.retire_samples`) on every path**
(the legacy delete, the SEEK proxy destroy, `sync_samples` for an id MySQL no longer returns, the nightly targeted
sync, the full sync):
- a `:Sample` graph_sync wrote (it carries `synced_at`) whose id MySQL no longer holds mirrors a row that is gone:
  its id, uuid, type and incident-edge count are appended to the run's `retired.tsv`, then it is `DETACH DELETE`d;
- a `:Sample` graph_sync never wrote (no `synced_at`: a v1.0 graph-only node, which may carry lineage MySQL never
  had) becomes `:OrphanSample`: `:Sample`, every `T_` label, `OF_TYPE` and `IN_PROJECT` removed, `orphaned_at` set,
  properties and DERIVED_FROM kept;
- existing `:OrphanSample` nodes are left as they are. Ghosts (a second node of a live id) are still deleted by the
  full sync, as now.

Relabelling everything would keep a deleted sample's metadata in the graph forever; deleting everything would
destroy, unarchived, the edges that exist only in the graph (1,064 edges on 349 legacy nodes locally). The line is
exact because only graph_sync sets `synced_at`.

## 10. The nightly targeted sync: how it finds what changed

### 10.1 What a watermark cannot see

`samples.updated_at` has no `ON UPDATE` default and is not moved by the native attribute API (WR-05), the
publication backfill (WR-16) or Rails' `refresh_samples` (WR-23). `projects_samples` has no timestamp and no primary
key; `investigations_projects` and the dmac context and map tables have no timestamp; a `group_memberships` leave is
a flag flip; a delete leaves no trace. `idx_updated_id (updated_at, id)` exists on the local snapshot, is created by
no file in the repository and is absent on the dev box.

### 10.2 The options

Costs are for 1.08M samples. The measured numbers are the POC's throwaway lane (MySQL with a 1 GiB buffer pool):
reading and projecting every sample took 25.7 s, gate G's MySQL scan 14 to 15 s, reading every project link 1.9 s,
streaming every Sample id from the graph 1.5 s, writing every sample 184.5 s. Everything else is an **estimate**; the
dev box's MySQL runs a 128 MiB buffer pool against a 1.7 GB `samples` table, so its scans can be 2 to 5 times slower.

| Option | Catches | Misses | Cost (estimated unless marked) |
|---|---|---|---|
| A. `(updated_at, id)` keyset watermark | writes that move `updated_at` | the attribute API, the publication backfill, `refresh_samples`, link-table changes alone, deletes, a type rename, a parent that appears after its child | seconds with the index; a full scan on the dev box without it |
| B. id-set diff | inserts, deletes | every update | about 3 s |
| C. **a per-sample source hash stored on the node, compared with the same hash computed from MySQL** | every change to the row's bytes, its type's title and value types, its project links and its assay links, however it was made; inserts and deletes fall out of the merge | a change made only in the graph (the weekly full sync repairs it) | about 30 to 60 s in the lane, 1 to 3 minutes on the dev box; writes only for what changed, about 6,000 samples a second at the lane's write rate |
| D. catalog rebuild | catalog changes, type renames, value-type changes | sample rows | about 2 s (about 6,000 rows) |
| E. full diffs of the small tables (memberships, projects, investigations and their links, studies, the assay map, `sops`) | all of them | nothing | under 2 s |

### 10.3 The choice: C with D and E

1. `catalog_sync` rebuilds the catalog (D); `sync_small_tables` rewrites the small tables and `relabel_for_maps`
   runs when `GraphMeta.label_maps_hash` differs (E).
2. Two ordered streams, merged by id with bounded memory: MySQL `samples` by primary-key keyset, merged with
   `projects_samples` ordered by `sample_id` and `assay_assets` Sample rows ordered by `asset_id`; and the graph's
   `MATCH (s:Sample) RETURN s.id, s.source_hash ORDER BY s.id`. The digest is computed in Python by the one function
   the projection uses when it writes the node: sha256 over the uuid, the title, the type's title and value-type
   signature, the raw `json_metadata` bytes, the sorted project ids and the sorted assay ids. Computing it in MySQL
   was rejected: the bytes would have to match Python's exactly, and the link tables could join only through
   correlated subqueries on a table with no index leading with `sample_id`.
3. Ids whose digests differ, and MySQL ids with no node, go to `sync_samples` in chunks; node ids MySQL lacks go to
   `retire_samples`.
4. When the changed samples carry uuids no node carried before, one more pass over `samples.json_metadata` finds old
   rows naming them and adds them. Above 1,000 new uuids this step is skipped and the weekly full sync covers it.
5. When more than 20% of the samples differ, the run stops before writing and schedules a full sync instead.

The watermark is not used for correctness; the run records the highest `samples.id` and `updated_at` it saw.

## 11. The weekly full sync

`run.full_sync` keeps its signature and order and gains: the `source_hash` and the parent lists on every node, the
label step over every edge (with per-property change counts in its report), the deletion rule in place of
`relabel_orphans` for graph-only `:Sample` ids, the graph-write lock for the whole run, a `graph_sync_run` row,
`GraphMeta.label_maps_hash`, and, on success, every outbox row enqueued before its start marked done. On a graph
whose Study nodes key SEEK studies on `id` (the dev box), the first 1.2 run moves them to `seek_study_id`.

## 12. State, the loop and its schedule

**`graph_sync_outbox`** (dmac): `id`, `kind`, `key`, `payload` (JSON: a batch's sample ids), `enqueued_at`,
`claimed_by`, `lease_expires_at`, `attempts`, `last_error`, `done_at`; `(kind, key)` unique, so a scheduled slot is
inserted once and repeated hook writes coalesce. Re-enqueueing a row resets it to pending; a worker marks a row done
only if `enqueued_at` is unchanged since its claim. A claim is a compare-and-set that counts an attempt; a failure
backs off (6 h for a full sync, 1 h otherwise); an expired lease is claimable again.

| Kind | Key | Enqueued by | The drain calls |
|---|---|---|---|
| `samples` | `sample:<id>`, or `batch:<job>:<n>` with ids in `payload` | the sample hooks, batch upload stage 5, orphan resolution, assay registration, the backfill command | `sync_samples` |
| `samples_of_type` | `type:<id>` | the attribute API, the legacy attribute editor, the sample-type proxy | `sync_samples_of_type` |
| `retire` | `sample:<id>` | the proxy destroy, the legacy delete | `retire_samples` |
| `catalog` | `*` | the attribute API, the legacy attribute editor, the sample-type proxy, the clade admin | `run.catalog_sync` |
| `assay_map`, `protocol_map` | `*` | the internal-assay admin, the assay proxy; the SOP proxy | `relabel_for_maps` |
| `isa`, `membership` | `*` | the project, investigation and study proxies; the users API | `sync_small_tables` |
| `reconcile`, `full`, `drift` | `slot:<date or ISO week>` | the schedule | a child `manage.py graph_sync` process |

**`graph_sync_run`** (dmac): `id`, `kind`, `started_at`, `finished_at`, `status` (`running`, `ok`, `failed`,
`refused`, `abandoned`, and `drift` for a drift run that found drift), `watermark_from`, `watermark_to`
(informational), `counts_json`, `drift_json`. Every run of `--full`, `--catalog`, `--reconcile`, `--drift` and
`--samples` records one, best-effort.

**The loop**, `manage.py graph_sync --loop`, leased and never exiting on a failure, in the style of
`run_assay_registration_jobs`: housekeeping, the schedule (a missed slot runs at the next start), the drain. `full`,
`reconcile` and `drift` run as child processes, so their memory returns when they end and a crash cannot kill the
loop. `--once` makes one pass. Schedule: reconcile 02:00 UTC, drift 02:30, full Sunday 03:00. Freshness: a full sync
within 8 days, a reconcile within 26 hours, the oldest pending outbox row within 1 hour.

**The launch line**, in `docker/scripts/entrypoint.sh` before `wait -n`, a restart loop in an `if` block so that
nothing about the loop can end the container:

```bash
if [ "${NEXTSEEK_GRAPH_SYNC_LOOP:-1}" = "1" ]; then
  ( while :; do uv run --no-sync python manage.py graph_sync --loop; sleep "${GRAPH_SYNC_RESTART_DELAY:-60}"; done ) &
fi
```

**On by default** (the operator's ruling): rollout goes to the local stack first, then the dev box, and a known-good
dev commit exists to roll back to. `NEXTSEEK_GRAPH_SYNC_LOOP=0` is the off switch. The loop writes nothing to a
graph that is not at the writer's version and never turns a graph into 1.2 by itself. It passes no
`--i-mean-the-live-graph`: the command accepts the stack's `neo4j` host for the loop and the read-only modes and
keeps refusing it for a manual `--full`, `--catalog`, `--reconcile` or `--samples` without the flag (the loop passes
the flag to its own children).

## 13. The drift check and the status endpoint

**`manage.py graph_sync --drift [--json]`** reads only. It runs the nightly targeted sync's detection without writing,
gate G's structural checks under their gate G names, and the freshness checks, and records the result. Exit 0 no
drift, 1 drift, 2 refused (including a graph not at the writer's version, with a printed reason, so a CI step can
skip), 3 could not complete. `--verify` and `--drift` read only, so they no longer need `--i-mean-the-live-graph`.

**`GET /nextseek_api/admin/graph-sync/status/`**, a native read-only ViewSet
(`nextseek_api/services/graph_sync_status.py`) that reads only the two dmac tables: `CsrfExemptSessionAuthentication`,
`BasicAuthentication`; `IsAuthenticated` plus `IsDjangoSuperuser` (401, 403); 200 with the latest run of each kind,
freshness per job, the outbox (pending and dead by kind, oldest pending and its age) and the latest drift result; 503
with the JSON:API error envelope when the tables cannot be read. Router prefix `admin/graph-sync` with one `status`
action and no list route, so the API root does not list it. Declared in `ci/routes.py` for **`local` and `dev`
only**: production runs a v1.0 graph without migration 0021, so a production smoke call would fail.
`OWNED_ROUTE_COUNT` moves from 169 to 170.

## 14. CI/CD

### 14.1 For what is already built

| # | Gap today | Change | Rows |
|---|---|---|---|
| CI-2 | the graph_sync and graph_search unit tests (10 files, about 319 tests) are collected by `ci-pytest.yml` but can never fail it: only `pytest ci/gate` blocks | `ci/blocking_lanes.py` (stdlib globs of test paths), run by a second blocking step in `ci-pytest.yml`, with a gate test that every glob matches; `makemigrations --check` in the same step (no migration check runs in CI today) | all |
| CI-3 | no CI job sends graph_search a request | `ci/smoke/test_graph_search.py`: a minimal POST as the smoke account on `local` and `dev`, asserting 200 and advanced_search's envelope; a parity-lite check (the same body to advanced_search, equal `total`) when the status endpoint reports a graph at the writer's version | E1 to E5 |
| CI-8 | graph_search is in neither `read_safe_endpoints.json` copy nor `_READ_POST_PATHS` | every file is under `NessieAI/`: **follow-up 1's change** | Nessie |
| CI-9 | gate G cannot see labels, `T_` labels on non-`:Sample` nodes or the parent lists | new checks in `verify.py`: `9.lineage.labels`, `10.samples.no_t_label_without_sample`, `11.samples.parent_lists` | E6, E8, E9, E17 |

### 14.2 New gates for the sync

| # | Gate | Where | Blocks |
|---|---|---|---|
| CI-1 | **The writer registry, driven by `ci/routes.py`.** `Route` gains a required `effect` (`reads`, `writes`, `external`, `n/a`) and `writers` (ids of `ci/writers.py` entries); the route gate's paste-ready skeleton emits `effect="UNCLASSIFIED"`, which `Route` refuses, so a new route or page (the graph-search UI pages being added on `feat/graph-search` included) fails until someone says what it writes. `ci/writers.py` (stdlib) declares every writer site with its tables, how it writes and either the hook it calls (with the function that must contain the call) or a category code (`RECONCILE_RAILS`, `RECONCILE_OPERATOR`, `RECONCILE_INSTALL`, `RECONCILE_DEAD`, `NO_GRAPH_EFFECT`, `GRAPH_SYNC_OWNER`). `ci/gate/writer_scan.py` finds the sites (the inventory's scan, made a gate). `ci/gate/test_writer_registry.py`: scan and declarations agree in both directions; every declared hook call is present in its function (AST); every `writes` route names its writers; a report-only tripwire lists `reads` routes whose view reaches a writer site through statically resolvable calls | `ci/gate`, Django lane | yes (the tripwire later) |
| CI-4 | **Drift after every rebuild.** `startup/steps/validate.py::check_graph_drift` runs `graph_sync --drift --json` in the app container with empty stdin (not through `compose_exec`, which raises on exit 1 and drops the JSON), skips with a printed line on a graph not at the writer's version, writes a `## Graph drift` section into the CI record, and makes `rebuild` exit non-zero at the end without stopping the smoke suite. Called from `startup/cli.py::rebuild` (app rebuilds on `local` and `dev`) and `startup/cli.py::ci` | startup | the rebuild's exit code |
| CI-5 | **Freshness.** `ci/smoke/test_graph_sync_status.py` reads the status endpoint with the superuser client (no `write` marker; it fails, not skips, when the credentials are missing) and fails on a stale full sync, a stale reconcile or an outbox row older than an hour. `local` and `dev` only | smoke | the smoke run |
| CI-6 | **Hook tests.** One unit test per hook site: the writer's success path enqueues the right row after its commit; its failure path enqueues nothing; a failing enqueue never raises into the writer | Django lane, in CI-2's globs | yes |
| CI-7 | **Write-lane E2E.** Behind `CI_WRITE_DESTRUCTIVE=1`: create an attribute, then poll the status endpoint until the outbox row is done and the reconcile count of differing samples is 0 | smoke write lane | the write lane |

## 15. Changes, each tied to its rows

| # | Change | Rows |
|---|---|---|
| C-01 | the two dmac tables (+ `payload`), migration 0021 checked against the heads | all |
| C-02 | outbox, run records, the lock, `hooks.enqueue` (never raises) | all |
| C-03 | projection: `source_hash`, the parent lists, `SYSTEM_KEYS`; schema 1.2 | E1 to E6 |
| C-04 | by-id readers and ordered streams in `sources` | E1 to E15 |
| C-05 | writer: `retire_samples`, per-child undeclared-edge archive, label SET, `(id, source_hash)` stream, `GraphMeta.label_maps_hash`, `_retry` moved in | E1, E7 to E9, E16, E17 |
| C-06 | `labels.py`, batch upload's label rule fed from MySQL, and its verification against the dev box and the local graph | E8, E9 |
| C-07 | `targeted.py` (section 7.1) | E1 to E15, E17 |
| C-08 | `run.full_sync` and `catalog_sync` changes (section 11) | all |
| C-09 | `reconcile.py` (section 10) and `drift.py` (section 13); gate G's new checks | all |
| C-10 | the command: `--loop`, `--once`, `--reconcile`, `--drift`, `--samples`; read-only modes accept the live host; `loop.py` and `schedule.py` | all |
| C-11 | the entrypoint line | all |
| C-12 | the status endpoint | E16 |
| C-13 | batch upload: stage 5 enqueue, stage 6 inline `sync_samples`, `neo4j_only`, the job's graph status; `upload_all` and its v1.0 writers deleted | E1 to E9, E15 |
| C-14 | orphan resolution: no graph write, enqueue after commit | E2, E6, E7 |
| C-15 | attribute API hook at `DjangoExecutionServices.record_commit` | E2, E10, E11 |
| C-16 | legacy pages: the upload and update paths enqueue, the delete enqueues `retire`, the two legacy attribute editor views enqueue `catalog` and `samples_of_type` after they write; `storeSampleNeo4j` and `deleteSampleNeo4j` deleted | E1, E2, E5, E7, E11, E17 |
| C-17 | proxy hooks: samples (create, patch, destroy), sample types, assays, studies, investigations, projects, sops; users API | E1, E2, E4, E8 to E15, E17 |
| C-18 | admin hooks: the four clade views, the four internal-assay views | E8, E10 |
| C-19 | assay registration enqueues instead of `recompute_for_samples` | E8 |
| C-20 | the publication backfill command enqueues; the three graph-only batch-upload scripts are deleted | E2, E6, E8 |
| C-21 | CI-1 (the registry, the `Route` fields, the scan) | all |
| C-22 | CI-2, CI-3, CI-9 | all |
| C-23 | CI-4, CI-5, CI-6, CI-7 | all |
| C-24 | `docs/neo4j-schema.md` 1.2; the READMEs of `graph_sync`, `batch_upload` and `ci` | all |

## 16. Out of scope

Per-project statistics and the `sensitive` flag (POC 11.1); Nessie's catalog reader, scope and read-safe lists
(follow-up 1); pinning the unlabelled lineage walks outside graph_sync to `:Sample` (deferred); retiring the legacy
attribute editor (it is hooked instead); a POST-read smoke tier for the 12 read-as-POST routes CI never sends;
managed `idx_updated_id` and `idx_samples_sample_type_id` (section 10 does not need them).

## 17. Rollout

1. Run the plan's four Workflow runs, with a review point after each.
2. **The only operator checkpoint: the label verification numbers.** Plan task V1 computes every label with the one
   rule from the merged MySQL, in the throwaway lane, and compares it (a) with the dev box's TCGA edge labels from
   the dev-box graph dump, expecting all 1,213,093 to match, and (b) with the labels on the local production edges
   from the local graph dump, reporting per-property matches and mismatches (this also shows how many labels an
   upload sheet supplied that MySQL never stored). Nothing writes labels to any live graph until (a) passes and the
   operator has seen both.
3. Per box, local first, then the dev box: deploy, then `graph_sync --full --i-mean-the-live-graph` at 1.2 (the loop
   refuses to write until then; uploads meanwhile leave their rows pending and report `graph: pending`). The dev box
   follows once follow-up 1's scope work (A1) is live there. A known-good dev commit is the rollback.
4. The loop is on by default; `NEXTSEEK_GRAPH_SYNC_LOOP=0` turns it off.

## 18. Decisions made

| # | Topic | Decision |
|---|---|---|
| R1 | Where the source hash lives | on the `Sample` node, as a system property (schema 1.2): it describes the node it sits on, and a node written by anything else simply mismatches and is re-synced |
| R2 | The loop | **on by default** (the operator's ruling: local first, then the dev box, with a known-good dev commit to roll back to). `NEXTSEEK_GRAPH_SYNC_LOOP=0` is the off switch; no per-box opt-in |
| R3 | Deletion | the rule of section 9 |
| R4 | `parent_titles`, `parent_title_hashes` | projection-owned, computed with batch upload's `enrich_parent_titles` rule, so orphan discovery keeps finding new uploads |
| R5 | DERIVED_FROM labels | graph_sync's labels equal what batch upload computes today from MySQL; any difference is reported by the verification (V1) and the full sync's counts, never changed silently |
| R6 | A SEEK assay with no internal-assay mapping | keep batch upload's fallback (the SEEK assay id and title) |
| R7 | The legacy attribute editor | kept, and hooked like every other current function: its two views enqueue after they write |
| R8 | Dev-box Study nodes keyed by SEEK `id` | the first 1.2 full sync moves them to `seek_study_id` |
| R9 | Schedule and thresholds | reconcile 02:00, drift 02:30, full Sunday 03:00 (UTC); full within 8 days, reconcile within 26 hours, outbox within 1 hour; the 20% guard |
| R10 | Stage 6's lock wait | 60 s, then `graph: pending` |
| R11 | Pinning other lineage readers to `:Sample` | deferred, out of scope |
| R12 | Findings made in passing | security findings are kept private and fixed in Run 1; the other defects are listed in section 20 |
| R13 | Schema version | 1.2, per the schema doc's versioning rule |

No question is left open. The one operator checkpoint is section 17, step 2.

## 19. What follow-up 1 and the POC need to know

- **Schema 1.2**: `SYSTEM_KEYS` gains `source_hash`; `GraphMeta` gains `label_maps_hash`; `schema_version` reads
  `"1.2"`. **Follow-up 1's design (its D7 and plan) uses the catalog context only when `schema_version` is exactly
  `"1.1"`. It must accept "1.1 or higher", or a 1.2 graph silently sends Nessie back to its old context.**
  `catalog_hash` is unchanged, so a catalog reader cached on it needs nothing else.
- **Until the label step lands, Nessie questions grouped by assay will not see TCGA in the local graph** (section
  1.1).
- The orphan `T_` label defect is latent (section 9); graph_search's results are unaffected today. Adding `:Sample`
  to graph_search's `MATCH (s:T_X)` source (`nextseek_api/graph_search/query.py`, the POC's file) would be a second
  guard.
- CI-8 (graph_search in both read-safe lists and `_READ_POST_PATHS`) is follow-up 1's: every file is under
  `NessieAI/`.
- `graph_sync --verify` and `--drift` stop requiring `--i-mean-the-live-graph`; `run.full_sync`, `run.catalog_sync`,
  `verify.gate_g` and every existing flag keep their meaning.
- Once 0021 lands, a migration on another branch depends on `0021_graph_sync_outbox_and_run`.

## 20. Found in passing

Security findings from this discovery are recorded privately by the operator and fixed in Run 1.

Other defects, left for issues:
- Orphan resolution writes the union of every parent-type key's tokens into the single `Parent` key.
- The SOP and data-file proxy creates return 500 after Rails has committed the create.
- The users API PATCH adds a membership and never ends the old one.
- `/seek/url/<x>/` and `/seek/remote/` fail on every request.

## 21. Risks

- **Memory.** The nightly and weekly syncs run as children inside the app container's 16 GiB cap beside the web
  server; the nightly one streams with bounded memory, the full sync holds one entry per sample in four indexes.
- **The inline sync lengthens batch-upload jobs** on a queue CC uploads share.
- **Labels may differ** from what is stored on edges that carried sheet-supplied labels; V1 reports it before any
  live write.
- **The scan is a heuristic.** It finds today's shapes; the routes-driven classification and the two-way diff make
  every listed site and every route answer for itself.
- **Staleness is bounded, not zero,** for Rails and hand SQL: up to a night, and a week for what only the graph held.
