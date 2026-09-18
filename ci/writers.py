"""Every writer of a graph source, declared once.

STDLIB ONLY, for the same reason ci/routes.py is: ci/routes.py names these ids in
its own entries, and it is imported by the smoke lane, which holds pytest,
requests and playwright and nothing else.

The graph is a projection of MySQL. A write to one of the tables below leaves the
graph behind until something tells graph_sync about it, so this file answers one
question for every such write in the tree: what tells graph_sync? Either the
writer calls a hook (`hook`, in the function named by `hook_site`, checked by AST
in ci/gate/test_writer_registry.py), or it carries a category code saying which
sync repairs it (`reconcile`). One of the two, never both and never neither.

The sites are found by ci/gate/writer_scan.py, and the gate diffs its result
against this file in both directions: a new writer site fails until it is declared
here, and an entry naming a site the scan no longer finds fails too. Add a site
without saying how the graph hears about it and the declaration is refused at
import.

The writer ids are the inventory's
(docs/superpowers/specs/2026-09-15-graph-search-sync-inventory.md section 2),
WR-01 to WR-28, and continue from WR-29 for a site that belongs to no inventory
row. Run `python3 ci/gate/writer_scan.py` for what the scan sees today.
"""
from __future__ import annotations

from dataclasses import dataclass

# What graph schema 1.2 reads, per nextseek_api/graph_sync/sources.py and the
# edge-label writers. `people` is here because it is a SEEK table the graph used
# to read: Person nodes come from group_memberships.person_id today, which is why
# the people proxy is declared NO_GRAPH_EFFECT rather than hooked.
GRAPH_SOURCE_TABLES = frozenset({
    # seek_production
    "samples", "sample_types", "sample_attributes", "sample_attribute_types", "projects_samples", "projects",
    "group_memberships", "work_groups", "people", "investigations", "investigations_projects", "studies", "assays",
    "assay_assets", "sops",
    # dmac
    "sample_types_context", "sample_types_clades", "clades", "sample_attributes_unique", "assays_internal_assays",
    "internal_assays",
})

# How a writer writes. The first seven are what the scan can see and are the kinds
# it reports; the last four belong to writers with no statement of their own in
# this repository.
HOW_CODES = frozenset({
    "sql",           # a SQL statement in a Python string
    "sql_file",      # a .sql file in the tree
    "orm",           # Django ORM write on a model whose db_table is a graph source
    "dbtable",       # the legacy table layer (storeOneRecord and its siblings)
    "seek_client",   # SeekAPIClient create_*/update_*/delete_*, committed by Rails
    "rails_runner",  # run_seek_rails_runner
    "cypher",        # a Cypher write, straight to Neo4j
    "rails",         # SEEK's own Rails UI, REST API or background jobs
    "external",      # a program outside this repository: an operator tool, hand SQL, a database client
    "shell",         # a shell script in the tree
    "none",          # no write of its own: it drives another writer's site, or only enqueues
})

# Why a writer needs no hook. A category code, never free text: this repository is
# public, and a code is also what lets a reader count the writers a given sync owns.
RECONCILE_CODES = frozenset({
    "RECONCILE_RAILS",     # SEEK writes it behind our back; only the nightly targeted sync sees it
    "RECONCILE_OPERATOR",  # an operator runs it by hand; the nightly or weekly sync repairs it
    "RECONCILE_INSTALL",   # install time only, before the operator's first full sync
    "RECONCILE_DEAD",      # unreachable code: no route, no caller, no command
    "NO_GRAPH_EFFECT",     # writes nothing the graph reads
    "GRAPH_SYNC_OWNER",    # graph_sync itself, the one writer of the graph
})

# Inventory rows whose sites no longer exist. Kept so that a reader of the
# inventory can tell a retired writer from one somebody forgot to declare;
# ci/gate/test_writer_registry.py refuses an id that is in both lists.
RETIRED = {
    "WR-17": "the three graph-only backfill scripts under nextseek_api/batch_upload/scripts/, deleted with "
             "batch upload's own graph writer when stage 6 moved to graph_sync",
}


@dataclass(frozen=True)
class Writer:
    """One writer of a graph source: where it writes, what it writes, and what tells graph_sync."""

    id: str                          # WR-NN, the inventory's id
    sites: tuple[str, ...]           # "path::symbol", or "path" for a .sql file; what the scan finds
    tables: tuple[str, ...]          # graph sources it writes; for a proxy, DECLARED, never inferred
    how: tuple[str, ...]             # HOW_CODES; the scan's kinds for its sites must be a subset
    hook: str | None = None          # the call that enqueues, e.g. "hooks.enqueue"
    hook_site: tuple[str, ...] = ()  # the function(s) that must contain that call
    reconcile: str | None = None     # a RECONCILE_CODE, for a writer that calls no hook
    note: str = ""                   # what this writer is, in one line

    def __post_init__(self) -> None:
        # Entries are authored with a bare string wherever there is one of something.
        # Normalise to a tuple so exactly one representation exists at run time:
        # iterating a string yields CHARACTERS, so sites="a/b.py::go" would become a
        # site per character, and every membership test below would silently pass.
        for field in ("sites", "tables", "how", "hook_site"):
            value = getattr(self, field)
            if isinstance(value, str):
                object.__setattr__(self, field, (value,))
            else:
                object.__setattr__(self, field, tuple(value))
        if not _id_ok(self.id):
            raise ValueError(f"{self.id}: an id is WR- and two digits, the inventory's own form")
        unknown = set(self.how) - HOW_CODES
        if unknown or not self.how:
            raise ValueError(f"{self.id}: how must be a non-empty subset of {sorted(HOW_CODES)}, not {self.how}")
        stray = set(self.tables) - GRAPH_SOURCE_TABLES
        if stray:
            raise ValueError(
                f"{self.id}: {sorted(stray)} is not a graph source. Declare only tables the graph reads; "
                f"a writer of anything else is not a writer for this gate."
            )
        if bool(self.hook) == bool(self.reconcile):
            raise ValueError(
                f"{self.id}: say EITHER the hook it calls OR the category code of the sync that repairs it. "
                f"Both means the hook is not trusted; neither means the graph never hears about this write."
            )
        if self.hook and not self.hook_site:
            raise ValueError(f"{self.id}: hook needs hook_site, the function the call must be in (checked by AST)")
        if self.hook_site and not self.hook:
            raise ValueError(f"{self.id}: hook_site needs the hook it is the site of")
        if self.reconcile and self.reconcile not in RECONCILE_CODES:
            raise ValueError(
                f"{self.id}: reconcile must be a category code from {sorted(RECONCILE_CODES)}, "
                f"not a description. This repo is public."
            )
        for site in self.sites + self.hook_site:
            _check_site(self.id, site)
        if not self.note:
            raise ValueError(f"{self.id}: a one-line note saying what this writer is")


def _id_ok(value: str) -> bool:
    return isinstance(value, str) and len(value) == 5 and value.startswith("WR-") and value[3:].isdigit()


def _check_site(writer_id: str, site: str) -> None:
    """A site is a repo-relative posix path, plus '::symbol' for a Python site."""
    path = site.split("::")[0]
    if site.count("::") > 1 or not path or path.startswith("/") or "\\" in site:
        raise ValueError(f"{writer_id}: {site!r} is not a 'path::symbol' site")
    if path.endswith(".sql") and "::" in site:
        raise ValueError(f"{writer_id}: {site!r} names a symbol in a .sql file")
    if not path.endswith((".py", ".sql")):
        raise ValueError(f"{writer_id}: {site!r} is not a .py or .sql path")


# The order is the inventory's. A hook is named as it is written at the call site,
# so a helper that wraps hooks.enqueue is named here and the gate follows it to its
# definition.
WRITERS: tuple[Writer, ...] = (
    Writer(id="WR-01",
           sites=("nextseek_api/batch_upload/insert_strategies.py::insert_samples_returning",
                  "nextseek_api/batch_upload/insert_strategies.py::insert_samples_fallback_select",
                  "nextseek_api/batch_upload/associations.py::batch_insert_projects_samples",
                  "nextseek_api/batch_upload/associations.py::batch_insert_assay_assets"),
           tables=("samples", "projects_samples", "assay_assets"),
           how=("sql",),
           hook="hooks.enqueue", hook_site="nextseek_api/batch_upload/insert.py::process_batches",
           note="batch upload stage 5, new samples; the batch's own outbox row goes in its transaction and this "
                "hook writes it after the commit when that was refused"),
    Writer(id="WR-02",
           sites=("nextseek_api/batch_upload/update.py::bulk_update_samples",
                  "nextseek_api/batch_upload/update.py::_bulk_delete_assay_links"),
           tables=("samples", "assay_assets"),
           how=("sql",),
           hook="hooks.enqueue", hook_site="nextseek_api/batch_upload/insert.py::process_batches",
           note="batch upload stage 5, update_existing: a deep merge of json_metadata and the assay links, "
                "enqueued by the same stage as WR-01"),
    Writer(id="WR-03",
           sites=(),
           tables=(),
           how=("none",),
           hook="hooks.enqueue", hook_site="nextseek_api/batch_upload/orchestrator.py::_build_neo4j_only_outcomes",
           note="batch upload neo4j_only: it writes no MySQL row at all and asks graph_sync for the ids the "
                "sheet's UIDs resolve to"),
    Writer(id="WR-04",
           sites=("nextseek_api/batch_upload/orphan_resolution.py::<module>",),
           tables=("samples",),
           how=("sql",),
           hook="hooks.enqueue", hook_site="nextseek_api/batch_upload/tasks.py::resolve_orphans_task",
           note="orphan resolution fills a sample's Parent key after a later batch created the parent; the "
                "statement is a module constant, which is why the site is the module"),
    Writer(id="WR-05",
           sites=("nextseek_api/attributes/executor.py::DjangoExecutionServices.apply_definitions",
                  "nextseek_api/attributes/metadata.py::rewrite_type_metadata"),
           tables=("sample_attributes", "samples"),
           how=("sql",),
           hook="hooks.enqueue",
           hook_site="nextseek_api/attributes/executor.py::DjangoExecutionServices.record_commit",
           note="the native attribute API: every create, rename and delete also normalises json_metadata on "
                "every sample of the type, without bumping updated_at"),
    Writer(id="WR-06",
           sites=("seek/views/samples.py::sampleAttributeSave",
                  "seek/views/samples.py::sampleAttributeDelete",
                  "seek/sample/table.py::DBtable_sample._updateSamplesMeta"),
           tables=("sample_attributes", "samples"),
           how=("dbtable",),
           hook="_enqueueAttributeGraphSync",
           hook_site=("seek/views/samples.py::sampleAttributeSave",
                      "seek/views/samples.py::sampleAttributeDelete"),
           note="the legacy attribute editor: no template in the tree calls its two endpoints any more, but "
                "both still write and both still bump updated_at"),
    Writer(id="WR-07",
           sites=("nextseek_api/services/samples.py::SampleProxyViewSet.create",
                  "nextseek_api/services/samples.py::SampleProxyViewSet.partial_update",
                  "nextseek_api/services/samples.py::SampleProxyViewSet.destroy"),
           tables=("samples", "projects_samples", "assay_assets"),
           how=("seek_client",),
           hook="hooks.enqueue",
           hook_site=("nextseek_api/services/samples.py::SampleProxyViewSet.create",
                      "nextseek_api/services/samples.py::SampleProxyViewSet.partial_update",
                      "nextseek_api/services/samples.py::SampleProxyViewSet.destroy"),
           note="the SEEK sample proxy; Rails commits the rows, by SEEK convention, and the destroy enqueues a "
                "retire rather than a sync"),
    Writer(id="WR-08",
           sites=("nextseek_api/services/sample_types.py::SampleTypeProxyViewSet.create",
                  "nextseek_api/services/sample_types.py::SampleTypeProxyViewSet.partial_update"),
           tables=("sample_types", "sample_attributes"),
           how=("seek_client",),
           hook="hooks.enqueue",
           hook_site=("nextseek_api/services/sample_types.py::SampleTypeProxyViewSet.create",
                      "nextseek_api/services/sample_types.py::SampleTypeProxyViewSet.partial_update"),
           note="the SEEK sample-type proxy; a rename moves every sample's type label, which is why it enqueues "
                "samples_of_type beside the catalog"),
    Writer(id="WR-09",
           sites=("nextseek_api/services/assays.py::AssayProxyViewSet.create",
                  "nextseek_api/services/assays.py::AssayProxyViewSet.partial_update",
                  "nextseek_api/services/studies.py::StudyProxyViewSet.create",
                  "nextseek_api/services/studies.py::StudyProxyViewSet.partial_update",
                  "nextseek_api/services/investigations.py::InvestigationProxyViewSet.create",
                  "nextseek_api/services/investigations.py::InvestigationProxyViewSet.partial_update",
                  "nextseek_api/services/projects.py::ProjectProxyViewSet.create",
                  "nextseek_api/services/projects.py::ProjectProxyViewSet.partial_update",
                  "nextseek_api/services/sops.py::SopProxyViewSet.create",
                  "nextseek_api/services/sops.py::SopProxyViewSet.partial_update"),
           tables=("assays", "assay_assets", "studies", "investigations", "investigations_projects", "projects",
                   "work_groups", "sops"),
           how=("seek_client",),
           hook="hooks.enqueue",
           hook_site=("nextseek_api/services/assays.py::AssayProxyViewSet.create",
                      "nextseek_api/services/assays.py::AssayProxyViewSet.partial_update",
                      "nextseek_api/services/studies.py::StudyProxyViewSet.create",
                      "nextseek_api/services/studies.py::StudyProxyViewSet.partial_update",
                      "nextseek_api/services/investigations.py::InvestigationProxyViewSet.create",
                      "nextseek_api/services/investigations.py::InvestigationProxyViewSet.partial_update",
                      "nextseek_api/services/projects.py::ProjectProxyViewSet.create",
                      "nextseek_api/services/projects.py::ProjectProxyViewSet.partial_update",
                      "nextseek_api/services/sops.py::SopProxyViewSet.create",
                      "nextseek_api/services/sops.py::SopProxyViewSet.partial_update"),
           note="the ISA and SOP proxies; the tables are declared because the route trace corrected the guesses "
                "a table map made (the project proxy cannot write memberships)"),
    Writer(id="WR-10",
           sites=("nextseek_api/services/users.py::UsersViewSet.create",
                  "nextseek_api/services/users.py::UsersViewSet.partial_update",
                  "nextseek_api/services/users.py::_compensate_failed_create",
                  "nextseek_api/services/users.py::_upsert_people_mirror"),
           tables=("people", "group_memberships", "work_groups"),
           how=("rails_runner", "orm"),
           hook="hooks.enqueue",
           hook_site=("nextseek_api/services/users.py::UsersViewSet.create",
                      "nextseek_api/services/users.py::UsersViewSet.partial_update"),
           note="the users admin API; a PATCH adds a membership and never ends one, and MEMBER_OF is what goes "
                "stale without the hook"),
    Writer(id="WR-11",
           sites=(),
           tables=("assay_assets",),
           how=("none",),
           hook="_enqueue_graph_sync",
           hook_site=("nextseek_api/assay_registration/service.py::register",
                      "nextseek_api/assay_registration/runner.py::run_one"),
           note="assay registration writes its links through WR-01's batch_insert_assay_assets and enqueues the "
                "samples whose edge labels move, instead of setting the plural lists itself"),
    Writer(id="WR-12",
           sites=("seek/sample/upload.py::SampleUploadMixin._storeSample",
                  "seek/sample/core.py::SampleCore._updateSampleProject",
                  "seek/sample/core.py::SampleCore.updateSingleSample",
                  "seek/dbtable_assay_assets.py::DBtable_assay_assets.storeSample_assay_asset",
                  "seek/dbtable_assay_assets.py::DBtable_assay_assets.storeDatafile_assay_asset",
                  "seek/dbtable_assay_assets.py::DBtable_assay_assets.updateSample_assay_asset"),
           tables=("samples", "projects_samples", "assay_assets"),
           how=("dbtable", "sql"),
           hook="enqueueSampleSync",
           hook_site=("seek/sample/upload.py::SampleUploadMixin._storeSample",
                      "seek/sample/upload.py::SampleUploadMixin._batchUpdateSample",
                      "seek/sample/upload.py::SampleUploadMixin._batchUpdateSampleAssociation"),
           note="the legacy sheet upload, reached by /seek/sampleupload/; the update paths enqueue too, which "
                "the graph write it used to do never covered"),
    Writer(id="WR-13",
           sites=("seek/sample/table.py::DBtable_sample._deleteOneSample",),
           tables=("samples", "projects_samples", "assay_assets"),
           how=("sql",),
           hook="enqueueSampleSync",
           hook_site="seek/sample/table.py::DBtable_sample._deleteOneSample",
           note="the legacy sample delete: eight statements in one transaction, then a retire row, where a "
                "swallowed DETACH DELETE used to be"),
    Writer(id="WR-14",
           sites=("dmac/dbtable_clades.py::DBtable_clades.new",
                  "dmac/dbtable_clades.py::DBtable_clades.update",
                  "dmac/dbtable_clades.py::DBtable_clades.delete",
                  "dmac/dbtable_sampletypesclades.py::DBtable_sample_types_clades.update",
                  "dmac/dbtable_sampletypesclades.py::DBtable_sample_types_clades.syncSampleTypes"),
           tables=("clades", "sample_types_clades"),
           how=("orm",),
           hook="hooks.enqueue",
           hook_site=("seek/views/admin.py::cladeSave",
                      "seek/views/admin.py::cladeDelete",
                      "seek/views/admin.py::cladeSampleTypesSave",
                      "seek/views/admin.py::cladesSyncSampleTypes"),
           note="the clade admin pages; the catalog carries SampleType.clade, so every one of the four views "
                "enqueues a catalog refresh"),
    Writer(id="WR-15",
           sites=("dmac/dbtable_internalassays.py::DBtable_internalassays.new",
                  "dmac/dbtable_internalassays.py::DBtable_internalassays.update",
                  "dmac/dbtable_internalassays.py::DBtable_internalassays.delete",
                  "dmac/dbtable_assaysinternalassays.py::DBtable_assaysinternalassays.update",
                  "dmac/dbtable_assaysinternalassays.py::DBtable_assaysinternalassays.syncAssays"),
           tables=("internal_assays", "assays_internal_assays"),
           how=("orm",),
           hook="hooks.enqueue",
           hook_site=("seek/views/admin.py::internalAssaySave",
                      "seek/views/admin.py::internalAssayDelete",
                      "seek/views/admin.py::assayAssociationSave",
                      "seek/views/admin.py::syncInternalAssays"),
           note="the internal-assay admin pages; a renamed internal assay moves the labels of every edge that "
                "names it, which is what the assay_map kind is for"),
    Writer(id="WR-16",
           sites=("nextseek_api/management/commands/backfill_publication_attributes.py::Command.handle",),
           tables=("samples",),
           how=("sql",),
           hook="enqueue_graph_sync",
           hook_site="nextseek_api/management/commands/backfill_publication_attributes.py::Command.handle",
           note="the publication backfill command with --apply; it does not bump updated_at, so without the "
                "hook only a full sync would ever see it"),
    Writer(id="WR-18",
           sites=("startup/steps/seed.py::load_neo4j_dump",),
           tables=(),
           how=("cypher", "external"),
           reconcile="RECONCILE_INSTALL",
           note="the install seed: load_mysql_dump feeds both schema dumps to the database client and "
                "load_neo4j_dump loads a graph into an empty Neo4j, both before the operator's first full sync"),
    Writer(id="WR-19",
           sites=(),
           tables=("sample_attributes_unique",),
           how=("sql",),
           reconcile="RECONCILE_INSTALL",
           note="the install schema fixups create the dmac context tables when they are missing; their "
                "statements build the table name at run time, so they are listed in UNRESOLVED_SITES"),
    Writer(id="WR-20",
           sites=("startup/seed/sql/sample_attributes_description.sql",
                  "startup/seed/sql/ROLLBACK_sample_attributes_description.sql",
                  "startup/seed/sql/sample_attributes_unique_data.sql",
                  "docs/archive/2026-08/publication-rollout/sample_publication_attributes/01_add_attributes.sql",
                  "docs/archive/2026-08/publication-rollout/sample_publication_attributes/02_catalogue.sql"),
           tables=("sample_attributes", "sample_attributes_unique"),
           how=("sql_file",),
           reconcile="RECONCILE_OPERATOR",
           note="hand SQL kept in the tree; nothing in the code applies any of these five files"),
    Writer(id="WR-21",
           sites=("scripts/context_gen.py::_mapping_parts",
                  "startup/seed/sql/sample_types_context.curated.sql"),
           tables=("sample_types_context", "internal_assays", "assays_internal_assays"),
           how=("sql_file", "sql", "external"),
           reconcile="RECONCILE_OPERATOR",
           note="the curated context tables. scripts/context_gen.py turns context/*.json into SQL but never "
                "connects to a database, so the write is always an operator applying that SQL by hand; "
                "render_update and render_seed build the table name at run time and are in UNRESOLVED_SITES. "
                "The .curated.sql seed files are held: no install step reads them until the curated content is "
                "signed off, and switching them on registers them as schema fixups (WR-19's mechanism). Every one "
                "of these tables feeds the graph catalog, so the sync that repairs them is the nightly or a full "
                "run, never the writer"),
    Writer(id="WR-22",
           sites=(),
           tables=("samples", "sample_types", "sample_attributes", "sample_attribute_types", "projects_samples",
                   "projects", "group_memberships", "work_groups", "people", "investigations",
                   "investigations_projects", "studies", "assays", "assay_assets", "sops"),
           how=("rails",),
           reconcile="RECONCILE_RAILS",
           note="SEEK's own Rails UI and REST API, used against SEEK directly; no code in this repository sees "
                "the write, so only the nightly targeted sync can"),
    Writer(id="WR-23",
           sites=(),
           tables=("samples",),
           how=("rails",),
           reconcile="RECONCILE_RAILS",
           note="Rails background jobs, SampleTypeUpdateJob above all: it re-saves every sample of a type with "
                "timestamps off, so not even updated_at moves"),
    Writer(id="WR-24",
           sites=(),
           tables=(),
           how=("external",),
           reconcile="RECONCILE_OPERATOR",
           note="operator tools outside this repository, and hand Cypher; they write the graph alone, so only "
                "the weekly full sync puts the one rule back"),
    Writer(id="WR-25",
           sites=("scripts/graph_search/load_graph_backup.py::<module>",
                  "scripts/graph_search/load_graph_backup.py::create_nodes",
                  "scripts/graph_search/load_graph_backup.py::create_rels",
                  "scripts/graph_search/parity.py::read_only_check",
                  "scripts/graph_search/merge_tcga.sql"),
           tables=("samples", "sample_types", "sample_attributes", "projects_samples", "projects",
                   "group_memberships", "work_groups", "people", "investigations", "investigations_projects",
                   "studies", "assays", "assay_assets", "sample_types_clades", "assays_internal_assays",
                   "internal_assays"),
           how=("cypher", "sql_file", "shell"),
           reconcile="RECONCILE_OPERATOR",
           note="the graph_search lane's own tools: they load a whole graph or a scratch MySQL, never a live "
                "instance, and parity.py's Cypher is a write the read session must refuse"),
    Writer(id="WR-26",
           sites=("nextseek_api/graph_sync/cypher.py::<module>",
                  "nextseek_api/graph_sync/run.py::<module>",
                  "nextseek_api/graph_sync/targeted.py::<module>"),
           tables=(),
           how=("cypher",),
           reconcile="GRAPH_SYNC_OWNER",
           note="graph_sync itself, the one writer of the graph; every statement it sends lives in these three "
                "modules"),
    Writer(id="WR-27",
           sites=(),
           tables=(),
           how=("none",),
           reconcile="NO_GRAPH_EFFECT",
           note="the Container-CC agent calls NExtSEEK endpoints as the requesting user, so it writes nothing "
                "of its own and inherits the hooks of whatever it calls"),
    Writer(id="WR-28",
           sites=("seek/sample/api.py::SampleApiMixin.apiInsertSample",
                  "seek/sample/api.py::SampleApiMixin.updateSampleDFurl",
                  "nextseek_api/batch_upload/update.py::update_sample_metadata",
                  "nextseek_api/batch_upload/update.py::smart_merge_assay_assets"),
           tables=("samples", "assay_assets"),
           how=("dbtable", "sql"),
           reconcile="RECONCILE_DEAD",
           note="dead code: the first two are reached only from api_app, which is never mounted, and the other "
                "two have no caller"),
    Writer(id="WR-29",
           sites=("nextseek_api/services/people.py::PeopleProxyViewSet.create",
                  "nextseek_api/services/people.py::PeopleProxyViewSet.partial_update"),
           tables=("people",),
           how=("seek_client",),
           reconcile="NO_GRAPH_EFFECT",
           note="the people proxy, split out of inventory WR-09: it writes people only, and Person nodes come "
                "from group_memberships.person_id, so nothing in the graph moves"),
    Writer(id="WR-30",
           sites=("nextseek_api/services/data_files.py::DataFileProxyViewSet.create",
                  "nextseek_api/services/data_files.py::DataFileProxyViewSet.partial_update"),
           tables=(),
           how=("seek_client",),
           reconcile="NO_GRAPH_EFFECT",
           note="the data-file proxy, split out of inventory WR-09: the assay_assets rows it makes link a data "
                "file to an assay, and the graph reads only a sample's"),
    Writer(id="WR-31",
           sites=("startup/seed/regenerate/dump_neo4j.py::main",),
           tables=(),
           how=("cypher",),
           reconcile="RECONCILE_INSTALL",
           note="the seed dump regenerator writes the Cypher file WR-18 installs; it reads a live graph and "
                "writes no graph of its own"),
)


# Sites where the scan can see a write but not which table it is on, because the
# table name is built at run time. None of them is a writer of its own: each is a
# generic record layer a declared writer above calls, or a write to a table the
# graph does not read. The gate diffs this list against the scan too, so a new
# module writing SQL through a variable table name fails until somebody says which
# of the two it is.
UNRESOLVED_SITES: tuple[str, ...] = (
    # The generic record layers. The caller holds the table name, and the caller's
    # own site is declared above: this is where WR-06, WR-12, WR-13, WR-14 and
    # WR-15 physically write.
    "dmac/dbtable.py::DBtable.storeOneRecord",
    "dmac/dbtable.py::DBtable.deleteOneRecord",
    "dmac/dbtable.py::DBtable.deleteRecordsConstraint",
    "dmac/dbtable.py::DBtable.__deleteRecords",
    "dmac/dbtable.py::DBtable.__saveRecords",
    "dmac/dbtable.py::DBtable.__processForm",
    "dmac/dbconnection.py::DBconnection.storeOneRecord",
    "dmac/dbconnection.py::DBconnection.saveOneRecord",
    "dmac/dbconnection.py::DBconnection.deleteOneRecord",
    "dmac/dbconnection.py::DBconnection.__updateRecordViaKeyword",
    "dmac/dbconn_mysql.py::DBconn_mysql.__insertOneRecord",
    "dmac/dbconn_mysql.py::DBconn_mysql.__insertRecords",
    "dmac/dbconn_mysql.py::DBconn_mysql.__updateRecords",
    "dmac/dbconn_mysql.py::DBconn_mysql.updateOneRecord",
    "dmac/dbconn_mysql.py::DBconn_mysql.__deleteRecords",
    "dmac/datagrid_custom.py::DataGrid.__save",
    # The same two layers again inside api_app, which is installed and imported but
    # never mounted, plus its sample-tree writer on a table the graph does not read.
    "api_app/dbconn_mysql.py::DBconn_mysql.__insertOneRecord",
    "api_app/dbconn_mysql.py::DBconn_mysql.__insertRecords",
    "api_app/dbconn_mysql.py::DBconn_mysql.__updateRecords",
    "api_app/dbconn_mysql.py::DBconn_mysql.updateOneRecord",
    "api_app/dbconn_mysql.py::DBconn_mysql.__deleteRecords",
    "api_app/remoteJob/dbconn_mysql.py::DBconn_mysql.__insertOneRecord",
    "api_app/remoteJob/dbconn_mysql.py::DBconn_mysql.__insertRecords",
    "api_app/remoteJob/dbconn_mysql.py::DBconn_mysql.__updateRecords",
    "api_app/remoteJob/dbconn_mysql.py::DBconn_mysql.updateOneRecord",
    "api_app/remoteJob/dbconn_mysql.py::DBconn_mysql.__deleteRecords",
    "api_app/updateTrees.py::saveSampleTree",
    "api_app/updateTrees.py::saveTreesToDB",
    "api_app/remoteJob/updateTrees.py::saveSampleTree",
    "api_app/remoteJob/updateTrees.py::saveTreesToDB",
    # A module the scan cannot parse, so cannot clear: it is Python 2, under the
    # app that is never mounted.
    "api_app/api_sampleParser.py",
    # The context generator's two table-agnostic renderers (WR-21). Both take the
    # table as an argument and read its name off cg.TABLES, so the scan sees a
    # statement with no literal table; and neither writes anything anyway, they
    # return SQL text for an operator to apply.
    "scripts/context_gen.py::_table_data",
    "scripts/context_gen.py::render_seed",
    # Install-time helpers, on tables the graph does not read (WR-19 and SEEK's own
    # settings table).
    "startup/steps/schema_fixups.py::_add_and_backfill",
    "startup/steps/schema_fixups.py::_write_ownership_marker",
    "startup/steps/schema_fixups.py::_delete_ownership_marker",
    "startup/steps/seek_settings.py::_insert",
    # Batch upload's own outbox row, written on the batch's connection inside the
    # batch's transaction. The table is graph_sync's, not a graph source.
    "nextseek_api/batch_upload/insert.py::enqueue_samples_outbox",
)


def _check_unique() -> None:
    """One entry per id, one writer per site, at import.

    A duplicate id makes the second entry unreachable through BY_ID, and a site
    claimed twice makes the both-directions diff in ci/gate/test_writer_registry.py
    pass while nobody owns the site.
    """
    seen_ids: set[str] = set()
    owner: dict[str, str] = {}
    for writer in WRITERS:
        if writer.id in seen_ids:
            raise ValueError(f"{writer.id}: declared twice in WRITERS")
        seen_ids.add(writer.id)
        for site in writer.sites:
            if site in owner:
                raise ValueError(f"{site}: claimed by {owner[site]} and {writer.id}; one writer per site")
            owner[site] = writer.id
    for site in UNRESOLVED_SITES:
        if site in owner:
            raise ValueError(f"{site}: declared by {owner[site]} and listed as unresolved")
    if len(set(UNRESOLVED_SITES)) != len(UNRESOLVED_SITES):
        raise ValueError("UNRESOLVED_SITES holds a duplicate")
    for writer_id in RETIRED:
        if writer_id in seen_ids:
            raise ValueError(f"{writer_id}: retired and declared; delete it from one of the two")


BY_ID: dict[str, Writer] = {w.id: w for w in WRITERS}
DECLARED_SITES: dict[str, str] = {site: w.id for w in WRITERS for site in w.sites}

_check_unique()
