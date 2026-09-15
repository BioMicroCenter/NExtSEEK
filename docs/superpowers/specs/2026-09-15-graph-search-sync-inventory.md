# graph sync discovery: the writer inventory

- Date: 2026-09-15
- Companion to `docs/superpowers/specs/2026-09-15-graph-search-sync-design.md` (the entry point). The design's
  section 5 table cites the writer ids `WR-01` to `WR-28` defined here.
- Tree: `feat/graph-search-sync` at `5d697c89` (feat/graph-search `aa9706d9` plus the docs). Everything below is
  read in code on that tree; nothing was run against a database. Where a claim comes from SEEK Rails conventions
  rather than code in this repository, it says so.

## 1. How the inventory was built

Two independent inventories, then a cross-check in both directions.

**Inventory 1, from the routes.** Every entry of `ci/routes.py` `REGISTRY` (171 entries, 169 owned by the resolver)
was traced from its URL to its view, through every HTTP method the view actually handles, down to the functions
that issue writes. The routes were split into five slices (the `nextseek_api` resources, the proxies, the
assistant routes, the legacy sample pages, the admin and project-level pages), each traced by a separate reader.
Result: 110 routes read only, 20 write only tables the graph does not read (sessions, jobs, `auth_user`, files),
and 41 write a graph source table.

**Inventory 2, from the code.** A standard-library AST scan (the prototype of the planned
`ci/gate/writer_scan.py`, design section 14) walked every non-test Python module under `nextseek_api`, `seek`,
`dmac`, `api_app`, `NessieAI` (history excluded), `scripts`, `startup`, `ci` and `docker` (1,365 modules, 744
test modules skipped) and every `.sql` file (18). It recognises:
- SQL in Python strings: `INSERT`/`REPLACE INTO`, `UPDATE ... SET`, `DELETE FROM`, `TRUNCATE` on a watched table,
  in plain, concatenated, `+`-joined and f-strings, docstrings skipped, the site being the innermost function;
- the legacy table layer: a class that sets `self.tablename` to a watched table, the mixins it is composed of
  (`seek/sample/table.py::DBtable_sample` binds eight), and calls of `storeOneRecord`, `deleteOneRecord`,
  `deleteRecordsConstraint` or `processRecords` on `self`, on a variable made from such a class, or through
  `self.tablemodel.objects`;
- ORM writes on the models whose `db_table` is watched (`seek/models/seek_mirror.py`, `seek/models/nextseek.py`);
- `SeekAPIClient` `create_*`, `update_*`, `delete_*` calls and `run_seek_rails_runner` calls;
- Cypher strings that write (`MERGE`, `CREATE`, `SET`, `DELETE`, `REMOVE`) beside a Cypher marker.

Result: 137 sites, 62 of them on a watched table (in 32 files), 30 Cypher write sites (in 14 files), and 28 sites
that write through a table name the scan cannot resolve (the generic record layers in `dmac/dbconnection.py`,
`dmac/dbconn_mysql.py`, the two `api_app` copies, `dmac/datagrid_custom.py`, and the startup fixup helpers).

**Watched tables** (what graph schema v1.1 reads, per `nextseek_api/graph_sync/sources.py` and the edge-label
writers): `seek_production.samples`, `sample_types`, `sample_attributes`, `sample_attribute_types`,
`projects_samples`, `projects`, `group_memberships`, `work_groups`, `people`, `investigations`,
`investigations_projects`, `studies`, `assays`, `assay_assets`, `sops`; `dmac.sample_types_context`,
`sample_types_clades`, `clades`, `sample_attributes_unique`, `assays_internal_assays`, `internal_assays`. `people`
feeds nothing today (Person nodes come from `group_memberships.person_id`), and `sops`, `assays`,
`assays_internal_assays` and `internal_assays` feed only the DERIVED_FROM labels, which graph_sync does not write
yet (design section 7).

**Recon check.** The 2026-09-14 recon's 18 sample writers (W1 to W18) and 9 catalog writers (C1 to C9) were the
seed list; each was re-read on this tree. No writer module changed between the recon's tree and this one; the
corrections in section 6 are errors or gaps in the recon itself, and how the old writers behave against a v1.1
graph.

## 2. The writer catalogue

`H` = a hook site exists in this repository; `N` = only the nightly targeted sync can see it; `F` = only the weekly
full sync repairs it; `-` = not a live writer. "Bumps" says whether a `samples` write moves `samples.updated_at`.

| Id | Writer | Sites (path::symbol) | Tables and operations | Reached by | Bumps | Graph today | Sync | Recon |
|---|---|---|---|---|---|---|---|---|
| WR-01 | Batch upload, new samples (stage 5) | `nextseek_api/batch_upload/insert_strategies.py::insert_samples_returning`, `::insert_samples_fallback_select`; `nextseek_api/batch_upload/associations.py::batch_insert_projects_samples`, `::batch_insert_assay_assets`; also `policies.py`, `permissions.py`, `prefetch.py::prefetch_project_sample_type_links` (tables the graph does not read) | `samples` create; `projects_samples` create; `assay_assets` create | `POST /nextseek_api/batch-upload/start/` then Celery `batch_upload.run` (`tasks.py::run_batch_upload_task`, `orchestrator.py`, `insert.py::process_batches`, `parallel.py::process_batches_parallel`) | yes | stage 6 `neo4j_sync.py::upload_all`, v1.0-shaped (section 5) | H | W1, C8 |
| WR-02 | Batch upload, update existing | `nextseek_api/batch_upload/update.py::bulk_update_samples`, `::_bulk_delete_assay_links`, plus WR-01's link inserts | `samples` update (deep merge); `assay_assets` create, delete; `projects_samples` create | as WR-01 with `update_existing` (request field, override or `BATCH_UPLOAD_UPDATE_EXISTING`) | yes | as WR-01 | H | W1 |
| WR-03 | Batch upload `neo4j_only` | `nextseek_api/batch_upload/orchestrator.py::_build_neo4j_only_outcomes`, then stage 6 | none in MySQL | as WR-01 with `neo4j_only=true` | n/a | stage 6 from the sheet's values | H | W2 |
| WR-04 | Orphan resolution | `nextseek_api/batch_upload/orphan_resolution.py::resolve_orphans` (`_UPDATE_METADATA_SQL`, `_DERIVED_FROM_CYPHER`) | `samples` update (`Parent`) | Celery `batch_upload.resolve_orphans` (`tasks.py::resolve_orphans_task`), dispatched after every upload with a non-empty identity map | yes | MERGE DERIVED_FROM with the singular assay labels set to null, sent before its MySQL transaction commits | H | W3 |
| WR-05 | Native attribute API | `nextseek_api/attributes/executor.py::DjangoExecutionServices.apply_definitions`; `nextseek_api/attributes/metadata.py::rewrite_type_metadata` | `sample_attributes` create, update, delete; `samples.json_metadata` of every sample of the type on every create, rename and delete (normalised to the declared set) | `POST /nextseek_api/attributes/batch-create/`, `batch-delete/`, `PATCH batch-patch/`; above 5,000 rows async through `dispatch_attribute_outbox`, Celery `attribute_mutations.run` and `recover_attribute_sync_jobs --loop` | **no** | none | H | C1, W9 |
| WR-06 | Legacy attribute editor | `seek/views/samples.py::sampleAttributeSave`, `::sampleAttributeDelete`; `seek/dbtable_sampleattribute.py::DBtable_sampleattribute.processRecords`; `seek/sample/table.py::DBtable_sample._updateSamplesMeta` | `sample_attributes` create, update, delete; `samples.json_metadata` of every sample of the type on save (rebuilt to the declared set); delete leaves the key in every sample | `/seek/attribute/save/`, `/seek/attribute/delete/` (no template in the tree calls them any more) | yes | none | H | C2, W10 |
| WR-07 | SEEK sample proxy | `nextseek_api/services/samples.py::SampleProxyViewSet.create`, `.partial_update`, `.destroy` (`SeekAPIClient.create_sample`, `update_sample`, `delete_sample`) | Rails commits `samples`, `projects_samples`, `assay_assets` (and policies, auth lookup) create, update, delete (by SEEK convention) | `POST /nextseek_api/samples/`, `PATCH`, `DELETE /nextseek_api/samples/{id}/` | Rails | none: a created sample has no node, a deleted one stays a live `:Sample` | H | W6 |
| WR-08 | SEEK sample-type proxy | `nextseek_api/services/sample_types.py::SampleTypeProxyViewSet.create`, `.partial_update` | Rails commits `sample_types`, `sample_attributes` (and `projects_sample_types`); then Rails `SampleTypeUpdateJob` re-saves every sample of the type with timestamps off (WR-23) | `POST /nextseek_api/sample_types/`, `PATCH /nextseek_api/sample_types/{id}/` | Rails | none: a rename leaves the SampleType title and every sample's `T_` label stale | H | C3, W7 |
| WR-09 | ISA and asset proxies | `nextseek_api/services/assays.py::AssayProxyViewSet.create`, `.partial_update`; `studies.py::StudyProxyViewSet`; `investigations.py::InvestigationProxyViewSet`; `projects.py::ProjectProxyViewSet`; `people.py::PeopleProxyViewSet`; `sops.py::SopProxyViewSet`; `data_files.py::DataFileProxyViewSet` (each `create`, `partial_update`; none exposes destroy) | Rails commits `assays` (and `assay_assets`), `studies`, `investigations` and `investigations_projects`, `projects` (the request model forbids members, so no memberships), `people`, `sops` (and Sop `assay_assets` rows), DataFile `assay_assets` rows (not read by the graph) | `POST`/`PATCH` on `/nextseek_api/assays/`, `studies/`, `investigations/`, `projects/`, `people/`, `sops/`, `data_files/` | n/a | none: new Study, Investigation and Project nodes wait for a sync; renames go stale | H | W8 (sops and data files missed) |
| WR-10 | Users admin API | `nextseek_api/services/users.py::UsersViewSet.create`, `.partial_update`, `::_compensate_failed_create` (`run_seek_rails_runner` with `SEEK_CREATE_RUBY`, `SEEK_PATCH_RUBY`, `SEEK_COMPENSATE_CREATE_RUBY`); `::_upsert_people_mirror` (ORM) | `people`, `group_memberships`, `work_groups` create, update (a PATCH adds a membership and never ends one) | `POST /nextseek_api/users/`, `PATCH /nextseek_api/users/{id}/` (superuser) | n/a | none: MEMBER_OF goes stale | H | W13 |
| WR-11 | Assay registration | `nextseek_api/assay_registration/executor.py` through `batch_upload/associations.py::batch_insert_assay_assets`; graph: `nextseek_api/assay_registration/graph.py::recompute_for_samples` (`RECOMPUTE_CYPHER`) | `assay_assets` create | `POST /nextseek_api/assay-registrations/` (in the request below `ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD`), else `manage.py run_assay_registration_jobs` (`runner.py::run_one`) | n/a | SET of the plural `internal_assay_ids`/`titles` on edges that already carry a singular label; never clears | H | W11 |
| WR-12 | Legacy sheet upload | `seek/views/upload.py::sampleUploadAjax`; `seek/sample/upload.py::SampleUploadMixin._storeSample`, `::storeSampleNeo4j`, `::_batchUpdateSample`, `::_batchUpdateSampleAssociation`; `seek/sample/core.py::SampleCore._updateSampleProject`, `::updateSingleSample`; `seek/dbtable_assay_assets.py::DBtable_assay_assets.storeSample_assay_asset`, `::storeDatafile_assay_asset`, `::updateSample_assay_asset` | `samples` create, update; `projects_samples` create (errors swallowed); `assay_assets` create, update, delete | `/seek/sampleupload/` (`/seek/samples/upload/` only renders the page) | yes | new samples only: `MERGE (s:Sample {id, uuid, type})` with no metadata, OF_TYPE by title, DERIVED_FROM with `SET r += $rels` and a non-standard `assay_title`; the update paths write no graph | H | W4 |
| WR-13 | Legacy sample delete | `seek/views/samples.py::sampleDelete`; `seek/sample/table.py::DBtable_sample.deleteSamples`, `::_deleteOneSample`, `::deleteSampleNeo4j` | `samples`, `projects_samples`, `assay_assets` delete (plus `sample_auth_lookup`, `policies`, `permissions`, `assets_creators`, `sample_resource_links`), one transaction | `/seek/samples/delete/` | n/a | `MATCH (s:Sample {id}) DETACH DELETE s` after the commit; a failure is swallowed by a bare `except` | H | W5 |
| WR-14 | Clade admin | `seek/views/admin.py::cladeSave`, `::cladeDelete`, `::cladeSampleTypesSave`, `::cladesSyncSampleTypes`; `dmac/dbtable_clades.py::DBtable_clades.new`, `.update`, `.delete`; `dmac/dbtable_sampletypesclades.py::DBtable_sample_types_clades.update`, `.syncSampleTypes` | `dmac.clades`, `dmac.sample_types_clades` create, update, delete (ORM) | `/seek/clade/save/`, `/seek/clade/delete/`, `/seek/clade/sampleTypes/save/`, `/seek/admin/clades/syncSampleTypes/` (supervisor) | n/a | none: `SampleType.clade` goes stale | H | C7 |
| WR-15 | Internal assay admin | `seek/views/admin.py::internalAssaySave`, `::internalAssayDelete`, `::assayAssociationSave`, `::syncInternalAssays`; `dmac/dbtable_internalassays.py::DBtable_internalassays.new`, `.update`, `.delete`; `dmac/dbtable_assaysinternalassays.py::DBtable_assaysinternalassays.update`, `.syncAssays` | `dmac.internal_assays`, `dmac.assays_internal_assays` create, update, delete (ORM, no cascade: a deleted internal assay leaves junction rows pointing at nothing) | `/seek/internal_assays/save`, `/delete`, `/assayAssociation/save`, `/seek/admin/internal_assays/syncInternalAssays` (supervisor) | n/a | none: `DERIVED_FROM.internal_assay_*` go stale (33 renamed titles, 40,557 edges on one of them, per the curation record) | H | W12 |
| WR-16 | Publication backfill command | `nextseek_api/management/commands/backfill_publication_attributes.py::Command.handle` (`--apply`) | `samples.json_metadata` update | operator, `manage.py backfill_publication_attributes --apply` | **no** | none | H | missed |
| WR-17 | Graph-only operator scripts | `nextseek_api/batch_upload/scripts/backfill_parent_titles.py::backfill`, `backfill_parent_title_hashes.py::write_hash_updates`, `backfill_shared_assays.py::apply` | none in MySQL | operator, standalone | n/a | SET `parent_titles`, `parent_title_hashes`, the plural assay lists | F (retire) | W17 |
| WR-18 | Install seed | `startup/steps/seed.py::load_mysql_dump`, `::load_neo4j_dump` | every table of both schemas | `./startup.sh install` and `reset` (the Neo4j load only into an empty graph) | n/a | a whole v1.0 graph | N, then the operator's first full sync | W17 (corrected) |
| WR-19 | Install schema fixups | `startup/steps/schema_fixups.py::apply_table_fixups` with `startup/seed/sql/assay_context.sql`, `projects_context.sql`, `sample_attributes_unique.sql`, `sample_type_requirements.sql`, `project_template_bundles.sql` | creates dmac context tables when missing (`sample_attributes_unique` is a source) | `./startup.sh install` only | n/a | none | N | C5 |
| WR-20 | Hand SQL in the repository | `startup/seed/sql/sample_attributes_description.sql`, `ROLLBACK_sample_attributes_description.sql`, `sample_attributes_unique_data.sql`; `docs/archive/2026-08/publication-rollout/sample_publication_attributes/01_add_attributes.sql`, `02_catalogue.sql` | `sample_attributes` update (descriptions), create (DOI and PMID on 115 types); `sample_attributes_unique` create | operator, by hand, never by code | n/a | none | N | C9 |
| WR-21 | Context tables by hand, and the coming context generator | `dmac.sample_types_context` (and `assay_context`, `projects_context`, not read by v1.1) | create, update, delete | hand SQL today; the context-content work's apply step when it lands | n/a | none | N now, H when the apply step exists | C5 |
| WR-22 | SEEK Rails UI and REST API | Rails controllers (samples, sample types, attributes, ISA, projects, memberships, sharing) | every SEEK table | a browser or a REST client on SEEK directly | Rails sets it on its own saves | none | N | W16, C4 |
| WR-23 | Rails background jobs | `SampleTypeUpdateJob` (`sample_type.rb::refresh_samples`, timestamps off), `SampleTemplateGeneratorJob`, auth lookup jobs | `samples` update (whether it rewrites `json_metadata` bytes is unverified) | Rails `after_save` of a sample type (WR-08, WR-22) | **no** | none | N | C3 |
| WR-24 | Out-of-repository operator tools | the curation plugin's stage 0 and relabel scripts, and hand Cypher (the 2026-08-28 and 2026-09-11 property strips) | graph only (DERIVED_FROM MERGE and label SET) | operator | n/a | yes, graph only | F (route through graph_sync) | W18 |
| WR-25 | graph_search lane scripts | `scripts/graph_search/load_live.sh` (replaces the live local MySQL core tables and the whole graph), `load_graph_backup.py` (a graph into an empty Neo4j), `merge_tcga.sh`/`.sql` (scratch MySQL only), `parity.py::read_only_check` (a write probe that must be refused) | lane or operator only | operator | n/a | whole graphs | N | new |
| WR-26 | graph_sync itself | `nextseek_api/graph_sync/run.py::full_sync`, `::catalog_sync`, through `writer.py` | none in MySQL | `manage.py graph_sync --full`, `--catalog` | n/a | v1.1, the owner | owner | new |
| WR-27 | Container-CC agent | the per-turn agent started by `NessieAI/cc/cc_engine.py`, which calls NExtSEEK endpoints as the requesting user | whatever the endpoint it calls writes | chat routes `/nextseek_api/nessie/query/`, `cc-assistant/query/async/` and their CC twins | n/a | none of its own | inherits the hooks of the endpoints it calls | W14 (corrected), W15 |
| WR-28 | Dead code | `seek/sample/api.py::SampleApiMixin.apiUploadSamples`, `::apiInsertSample`, `::updateSampleDFurl` (via `seek/dbtable_data_files.py::apiUploadFile`), reached only from `api_app/views.py`, which is never mounted; `nextseek_api/batch_upload/update.py::update_sample_metadata`, `::smart_merge_assay_assets` (no caller); `neo4j_sync.py::delete_derived_from_for_uuids` (test only); `nextseek_api/batch_delete/` (models and tests only) | `samples`, `assay_assets` | nothing | - | - | - (registered dead) | new |

Not graph sources, seen on the way: `manage.py derive_sample_type_requirements` (`dmac.sample_type_requirements`,
C6), batch upload's `projects_sample_types` insert (C8, harmless on v1.1), policies and permissions, the login
views (`auth_user`, `django_session`), chat sessions and job tables, `scripts/generate_assay_context_seed.py` (emits
SQL, writes nothing), `startup/steps/seek_settings.py::_insert` (SEEK's settings table), `api_app/updateTrees.py`
(`seek_sample_tree`, unmounted).

Counts: 28 entries. 16 have a hook site in this repository (WR-01 to WR-16), 8 can be seen only by the nightly
targeted sync (WR-18 to WR-23, WR-25, and WR-21 until its apply step lands), 2 write the graph alone and are
repaired only by the weekly full sync (WR-17, WR-24), 1 is the owner (WR-26), 1 inherits other writers' hooks
(WR-27), and 1 is dead code (WR-28).

## 3. Inventory 1: the routes that write

Every `REGISTRY` pattern not listed here reads only (110 patterns). The raw per-route trace (view, every method, every
writer site, confidence) is kept with the handoff, outside the repository.

| Pattern | Writers |
|---|---|
| `^nextseek_api/^^batch-upload/start/$` | WR-01, WR-02, WR-03, then WR-04 |
| `^nextseek_api/^^attributes/batch-create/$`, `batch-delete/$`, `batch-patch/$` | WR-05 |
| `^nextseek_api/^^samples/$` (POST), `^nextseek_api/^^samples/(?P<uid>[^/]+)/$` (PATCH, DELETE) | WR-07 |
| `^nextseek_api/^^sample_types/$`, `^nextseek_api/^^sample_types/(?P<uid>[^/]+)/$` | WR-08 |
| `^nextseek_api/^^assays/$` and detail, `studies`, `investigations`, `projects`, `people`, `sops`, `data_files` (list and detail, 14 patterns) | WR-09 |
| `^nextseek_api/^^users/$` and detail | WR-10 |
| `^nextseek_api/^^assay-registrations/$` | WR-11 |
| `^nextseek_api/^^nessie/query/$`, `nessie/query/cc/$`, `cc-assistant/query/async/$`, `cc-assistant/cc/query/async/$` | WR-27 (inherits the hooks of the endpoints it calls) |
| `^seek/^sampleupload/` | WR-12 |
| `^seek/^samples/delete/` | WR-13 |
| `^seek/^attribute/save/`, `^seek/^attribute/delete/` | WR-06 |
| `^seek/^clade/save/$`, `clade/delete/$`, `clade/sampleTypes/save/$`, `^seek/^admin/clades/syncSampleTypes/$` | WR-14 |
| `^seek/^internal_assays/save$`, `internal_assays/delete$`, `internal_assays/assayAssociation/save$`, `^seek/^admin/internal_assays/syncInternalAssays$` | WR-15 |

Routes that write only tables the graph does not read (20): the four login and logout patterns, `admin/retrieve`
(an xlsx under `MEDIA_ROOT`), the assistant sessions pair, `nessie/uploads`, `assistant/query`, `query/async`,
`report`, `generate-submission`, `build-upload-xlsx`, `schema_rag/ingest` and `retrieve` (DuckDB files),
`evaluator/retry`, `cc-assistant/upload` (moves files), and the three job-cancel routes (assay registrations,
attributes, batch upload; a batch-upload cancel after stage 5 leaves committed samples with no graph write).

## 4. Inventory 2: the code-scan sites and what each is

| Scan sites | Is |
|---|---|
| the 62 watched-table sites in `nextseek_api/batch_upload/`, `nextseek_api/attributes/`, `nextseek_api/services/*` proxies, `nextseek_api/services/users.py`, `seek/sample/*`, `seek/dbtable_assay_assets.py`, `seek/views/samples.py`, `dmac/dbtable_*` | WR-01 to WR-16 |
| `nextseek_api/management/commands/backfill_publication_attributes.py::Command.handle` | WR-16 |
| `startup/seed/sql/*.sql`, `docs/archive/2026-08/publication-rollout/*/0*.sql` | WR-19, WR-20 |
| `scripts/graph_search/merge_tcga.sql`, `load_graph_backup.py`, `parity.py::read_only_check` | WR-25 |
| `seek/sample/api.py::SampleApiMixin.apiInsertSample`, `::updateSampleDFurl`; `update.py::update_sample_metadata`, `::smart_merge_assay_assets` | WR-28 (dead) |
| the Cypher sites in `neo4j_sync.py` (13), `orphan_resolution.py`, `assay_registration/graph.py`, `seek/sample/upload.py`, `seek/sample/table.py`, the three batch-upload scripts, `startup/steps/seed.py` | WR-01 to WR-04, WR-11 to WR-13, WR-17, WR-18 |
| `nextseek_api/graph_sync/cypher.py` (41 statements) | WR-26 |
| `NessieAI/chat_nextseek/src/chat_nextseek/helpers/tools/neo4j.py::tool_neo4j_query` | Nessie's graph read tool, follow-up 1's (item A1); not a writer |
| `dmac/dbconnection.py`, `dmac/dbconn_mysql.py`, `dmac/datagrid_custom.py::DataGrid.__save`, the `api_app` record layers | infrastructure: the generic record layer the legacy table classes write through, not a site of its own |
| `startup/steps/schema_fixups.py::_add_and_backfill`, `::_write_ownership_marker`, `::_delete_ownership_marker`, `startup/steps/seek_settings.py::_insert` | install-time helpers on tables the graph does not read |
| `NessieAI/chat_nextseek/src/chat_nextseek/agents/graph.py` (a clause regex), `seek/views/samples.py::retrieveSamples` (`processRecords(..., 'retrieve')`), `startup/seed/regenerate/dump_neo4j.py` (writes a dump file from a read) | false positives; the CI scanner must skip strings compiled as regular expressions and a `processRecords` whose operation is a read |

## 5. Cross-check: what only one inventory found

Found by the routes only:
1. **The Container-CC agent** (WR-27). It calls NExtSEEK endpoints as the requesting user, so no code of its own
   writes; a static scan cannot see it by construction, and the endpoints' own hooks cover it.
2. **What Rails writes behind a proxy.** The scan can only name the resource. The route trace corrected two guesses
   the scan's table map made: the project proxy cannot write memberships, and the people proxy writes `people`
   only. So the CI registry must declare a proxy's tables, never infer them (design section 14).

Found by the scan only:
1. **Dead writers** (WR-28): the `SampleApiMixin` insert and data-file paths are reached only from the unmounted
   `api_app`, and two `update.py` helpers have no caller.
2. **Writers that are not routes by nature**: the publication backfill command (WR-16), the graph-only scripts
   (WR-17), the install seed and fixups (WR-18, WR-19), hand SQL (WR-20) and the lane scripts (WR-25).
3. **Stage 6's individual graph functions** (`bulk_merge_*`, `ensure_constraints`, the stale-edge deletes), which
   the route trace names only as `upload_all`.

Found by neither (no code in this repository): the SEEK Rails UI and REST API (WR-22), Rails jobs (WR-23),
out-of-repository tools (WR-24), and the script that loaded TCGA onto the dev box. Only the nightly targeted sync
and the weekly full sync can see them.

## 6. Corrections to the recon of 2026-09-14

1. **W1**: stage 6 also deletes stale DERIVED_FROM edges and runs schema DDL on every upload; its
   `ensure_constraints` re-creates `sample_uuid_unique`, which every v1.1 full sync drops; it writes
   `projects_sample_types` and deletes unused policies; its `Study {id}` MERGE collides with v1.1's keying of SEEK
   studies on `seek_study_id`. On a v1.1 graph a sample it writes is invisible to graph_search even when stage 6
   succeeds (no `T_` label, `project_ids` or `search_text`), and its `MERGE (s:Sample {uuid})` breaks on a
   duplicated uuid and aborts the rest of the stage. `s += row.properties` is still in
   `neo4j_sync.py::bulk_merge_nodes` on this branch and on origin/dev.
2. **W3**: the Neo4j MERGE runs inside the MySQL transaction (the graph commits first), it nulls the singular labels
   of an edge that already existed, and it writes the union of every parent-type key's tokens into the single
   `Parent` key.
3. **W4**: the writing route is `/seek/sampleupload/`; `/seek/samples/upload/` only renders. A row with a supplied
   UID upserts `samples` with no `projects_samples`, `assay_assets` or graph write, and overwrites `created_at`. The
   three-property MERGE raises against v1.1's `Sample.id` constraint when an existing node's uuid or type differs.
4. **W5**: the transaction also deletes `sample_resource_links`.
5. **W8**: it misses `SopProxyViewSet` (the source of `protocol_title`) and `DataFileProxyViewSet`. No ISA proxy
   exposes destroy. The project proxy cannot write memberships. The SOP and data-file creates return 500 after a
   committed Rails create (an unbound local on the no-files branch).
6. **W9 and C1**: the metadata rewrite also runs on every create (each sample of the type gains the new key as
   `""`) and drops every key outside the declared set. The hook site the synthesis corrected,
   `DjangoExecutionServices.record_commit`, holds; the enqueue goes after its compare-and-set.
7. **W10 and C2**: the legacy editor does bump `updated_at`; a delete leaves the key in `json_metadata`; nothing in
   the tree calls the two endpoints any more.
8. **W11**: the recompute skips edges graph_sync creates (no singular label) and keeps the last junction row where
   batch upload keeps the smallest internal id.
9. **W13**: it misses the ORM write to `people` (`_upsert_people_mirror`) and `SEEK_COMPENSATE_CREATE_RUBY`; a PATCH
   adds memberships and never ends one.
10. **W14**: `assistant/api-write/` writes nothing: the tool it sends through sends only three search POSTs. Chat
    turns reach NExtSEEK writes through the Container-CC agent calling the endpoints themselves (WR-27).
11. **W15**: `cc_assistant.upload` only moves files; `build-upload-xlsx` also saves a bundle into the chat session.
12. **W17**: the seed loader is `load_neo4j_dump` (install into an empty graph only), not `parse_neo4j_cypher_dump`,
    and `load_mysql_dump` is a writer the recon missed.
13. **W18**: `Investigation.project_id` and CHILD_OF are no longer writerless: graph_sync writes the first and
    deletes the second.
14. **C9**: `01_add_attributes.sql` and `02_catalogue.sql` are in the tree, under
    `docs/archive/2026-08/publication-rollout/sample_publication_attributes/` (the recon's verifier said they were
    not).
15. **Missed writers**: `backfill_publication_attributes --apply` (no `updated_at` bump), `SopProxyViewSet`,
    `_upsert_people_mirror`, `load_mysql_dump`, `graph_sync` itself, `load_live.sh`, `load_graph_backup.py`,
    `merge_tcga`.
16. **The first sync draft** (`9c082ac7`): its hook table omitted the four internal-assay admin views, and its delta
    relabelled edges from the child side only, where an assay-set change moves the labels of a sample's edges in
    both directions.

## 7. Direct graph writers today, and what each becomes

| Writer | Today | Becomes |
|---|---|---|
| Batch upload stage 6 (`neo4j_sync.py::upload_all`: `ensure_constraints`, index DDL, `bulk_merge_nodes`, `bulk_merge_sample_type_nodes`, `delete_stale_derived_from_for_uuids`, `bulk_merge_relationships`, `bulk_merge_of_type_relationships`, `bulk_merge_study_nodes`, `bulk_merge_investigation_nodes`, `bulk_merge_in_investigation_relationships`, `bulk_merge_in_study_relationships`) | v1.0 shape: uuid key, `s +=` of the sheet's metadata, SampleType by title, Study by SEEK id, labels from the sheet; failure swallowed, job still SUCCESS | deleted. Stage 5 enqueues the committed ids; stage 6 calls `graph_sync.targeted.sync_samples(ids)` (design section 8) |
| `neo4j_sync.py::_retry` | imported by `graph_sync/writer.py` | moves into `graph_sync` |
| Batch upload `neo4j_only` | stage 6 from the sheet | `sync_samples` of the ids the sheet's UIDs resolve to; the sheet's metadata is ignored |
| Orphan resolution `_DERIVED_FROM_CYPHER` | edge MERGE with null labels, before the MySQL commit | removed; after its commit it enqueues the resolved children |
| Assay registration `recompute_for_samples` | plural labels on already-labelled edges | removed; the registration enqueues the touched samples, and graph_sync's label step (design section 7) relabels their edges |
| Legacy upload `storeSampleNeo4j` | bare node, OF_TYPE by title, `r +=` | removed; `_storeSample` and the update paths enqueue the ids |
| Legacy delete `deleteSampleNeo4j` | DETACH DELETE, failure swallowed | removed; `_deleteOneSample` enqueues a retire after its commit |
| `backfill_parent_titles.py`, `backfill_parent_title_hashes.py`, `backfill_shared_assays.py` | graph-only SETs | deleted; graph_sync computes both lists and the labels |
| `graph_sync/cypher.py::RELABEL_ORPHANS` and `writer.relabel_orphans` | keep every `T_` label. Latent today: the relabel runs before the samples step and graph-only ids are never projected, so today's orphans carry no `T_` label and graph_search is unaffected; it would bite once a synced sample is deleted from MySQL and a later full sync relabels it, which would then still match `MATCH (s:T_X)` | become the one retire function (design section 9) |
| `startup/steps/seed.py::load_neo4j_dump` | installs a v1.0 graph | stays; the install is followed by the operator's first `graph_sync --full`, or the seed graph is regenerated at the writer's version |
| `scripts/graph_search/load_graph_backup.py`, `load_live.sh` | operator tools | stay; `load_live.sh` should check the dump's `GraphMeta.schema_version` |
| The curation plugin's stage 0 and relabel (out of the repository) | DERIVED_FROM MERGE and label SET on production | retired in favour of graph_sync's label step once it lands; until then the weekly full sync overwrites what they set with the one rule |
