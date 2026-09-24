# Neo4j graph schema

The document of record for the NExtSEEK sample graph: what it holds (v1.0), what the graph_search work builds
(v1.1), and what keeping it in sync adds (v1.2). Querying Neo4j over HTTP, Browser or bolt is in
[`neo4j-programmatic-access.md`](neo4j-programmatic-access.md).
The graph agent reads the live catalog on every turn; the committed `neo4j_schema.json` it falls back to is a
capture (`scripts/graph_schema_fallback.py` regenerates it), not this document.

Counts are dated measurements; re-measure before relying on one. "Measured" is the graph now; the v1.0 section
records the graph as found on 2026-09-14, before v1.1.

## Measured, 2026-09-24

The local graph (the production snapshot plus TCGA) at schema 1.2, `catalog_hash` 7c3840b3d0be, synced
2026-09-24T13:30Z, read only.

| Label | Count | Note |
|---|---:|---|
| `Sample` | 1,084,762 | every one carries `synced_at`; no id sits on two nodes |
| `SampleType` | 118 | 111 live, 7 deprecated |
| `Attribute` | 3,569 | 2,671 declared with values, 860 declared and empty, 38 found only in data; 1,170 distinct titles hold a value |
| `Study` | 97 | 48 published, 8 paper-level unpublished, 41 SEEK studies |
| `Investigation` | 17 | |
| `Project` | 14 | |
| `Person` | 108 | |
| `OrphanSample` | 270 | |
| `GraphMeta` | 1 | |

| Pattern | Count |
|---|---:|
| `(:Sample)-[:DERIVED_FROM]->(:Sample)` | 2,014,307 |
| `DERIVED_FROM` with an `OrphanSample` at either end | 979 |
| `(:Sample)-[:OF_TYPE]->(:SampleType)` | 1,084,762 |
| `(:OrphanSample)-[:OF_TYPE]->(:SampleType)` | 220 |
| `(:Sample)-[:IN_STUDY]->(:Study)` | 1,082,863 |
| `(:OrphanSample)-[:IN_STUDY]->(:Study)` | 270 |
| `(:Study)-[:IN_INVESTIGATION]->(:Investigation)` | 97 |
| `(:Sample)-[:IN_PROJECT]->(:Project)` | 1,127,621 |
| `(:Investigation)-[:IN_PROJECT]->(:Project)` | 17 |
| `(:SampleType)-[:HAS_ATTRIBUTE]->(:Attribute)` | 3,569 |
| `(:Person)-[:MEMBER_OF]->(:Project)` | 166 |
| `CHILD_OF` | 0 |

- Samples by clade: Analyzed 557,747, Processed 212,590, Source 202,812, Raw 111,613.
- The longest DERIVED_FROM chain is 11 hops, over every edge between two Samples; there is no cycle.
- DERIVED_FROM labels: 1,998,154 edges carry the three singular assay fields and 655,184 a `protocol_title`; 5,026
  carry neither an assay nor a protocol; 3,645 name more than one assay.
- Indexes: 631 range, the fulltext `sample_search_text` and the two lookup indexes; the ten uniqueness constraints
  of v1.1.

### Known issues

- `internal_assay_title` names one assay per edge. On the 3,645 edges several assays share, the others are only in
  `internal_assay_titles`, and five assay titles appear nowhere else, so a query tests both:
  `r.internal_assay_title = $assay OR $assay IN coalesce(r.internal_assay_titles, [])`.
- 781,392 DERIVED_FROM edges between two Samples carry no plural lists (`plural_missing`, v1.2 rule 5); only
  `--apply-label-changes` writes them.
- A SEEK study (one with `seek_study_id`) carries only `title` and `seek_study_id`: no `id`, `DOI`, `PMID` or
  `description`.
- The 270 orphans predate 1.2 and keep edges the 1.2 rule removes (the v1.2 `OrphanSample` row).
- LYS is deprecated in the catalog while 12 samples carry `:T_LYS`. The graph agent's type index leaves deprecated
  types out, so it never sees them.
- 435 samples belong to no project (`project_ids` is an empty list) and 3,413 to no study.

## v1.0: the graph as found (2026-09-14)

### Nodes

| Label | Count | Properties | Keyed by (the writer's MERGE) |
|---|---:|---|---|
| `Sample` | 166,569 | `id` (SEEK `samples.id`), `uuid` (the sample UID), `type` (sample type code), and 15 lineage keys: `UID`, `Protocol`, `Parent`, `parent_titles`, `parent_title_hashes`, and the `*Parent` family | `uuid` |
| `SampleType` | 104 | `title` (the code); `id` on 55 of 104 | `title` |
| `Study` | 56 | `id`, `title`, `description`, `DOI`, `PMID` | `id` |
| `Investigation` | 8 | `id`, `title`, `description`, `project_id` (7 of 8) | `id` |

- Local `Study` nodes are paper-level studies with graph-local ids: of the 48 ids shared with SEEK's `studies`, only 4
  carry SEEK's title, and 8 ids exist only in the graph. On the dev box, `Study` is SEEK's study.
- No `Person`, `Project` or `Assay` node exists. A project appears only as `Investigation.project_id`.
- No sample metadata sits on local nodes. The dev box's Sample nodes carry raw metadata (720 keys), because batch
  upload writes the whole `json_metadata` with `s += row.properties`.

### Relationships

| Pattern | Count | Notes |
|---|---:|---|
| `(:Sample)-[:DERIVED_FROM]->(:Sample)` | 802,231 | child to parent. Properties `child_id`, `parent_id`, `assay_id`, `internal_assay_id`, `internal_assay_title`, `protocol_id`, `protocol_title`, and plural `internal_assay_ids`, `internal_assay_titles` |
| `(:Sample)-[:CHILD_OF]->(:Sample)` | 742,534 | legacy; no writer and no code reader. 742,408 duplicate a DERIVED_FROM; 881 pairs are declared nowhere else |
| `(:Sample)-[:IN_STUDY]->(:Study)` | 162,457 | 5,626 samples have none; 1,300 have two or three |
| `(:Study)-[:IN_INVESTIGATION]->(:Investigation)` | 56 | one per study |
| `(:Sample)-[:OF_TYPE]->(:SampleType)` | 12,240 | about 7% of samples; readers use `s.type` instead. Complete on the dev box |

### Constraints and indexes

Locally: none, apart from the two default LOOKUP indexes. `nextseek_api/batch_upload/neo4j_sync.py` declares five
uniqueness constraints (`Sample.id`, `Sample.uuid`, `SampleType.title`, `Study.id`, `Investigation.id`), but the
`Sample.id` one cannot be created while 79 ids sit on two nodes each, and the seed loader
(`startup/steps/seed.py`) carries none. The dev box has all five, plus a FAILED `sample_parent_title_hashes` index.

### Writers

Batch upload's stage 6 (`nextseek_api/batch_upload/neo4j_sync.py`) is the main writer: Sample nodes, SampleType
nodes, DERIVED_FROM, OF_TYPE, IN_STUDY, Study, Investigation and IN_INVESTIGATION. DERIVED_FROM pairs come from the
parent tokens rule in `nextseek_api/batch_upload/helpers.py` (any key containing "parent", split on `;`, UIDs only).
Orphan resolution, assay registration and the legacy sample pages also write; nothing writes a Person, a Project or
`Investigation.project_id`. No command rebuilds the graph from MySQL.

### Known defects

- 79 ghost Sample nodes share an `id` with a live sample and are not in MySQL.
- 270 other graph-only Sample ids (349 nodes) carry 1,064 lineage edges MySQL does not declare.
- `s += row.properties` adds keys and never removes one, so a key deleted from `json_metadata` stays on the node.
- A DERIVED_FROM whose child or parent node is absent is dropped silently; the drop is counted but not repaired.
- Walking `IN_STUDY`, `IN_INVESTIGATION` and `project_id` does not reproduce `projects_samples`: it misses 49,621 of
  209,096 sample-project pairs and every project-6 sample.

v1.1 deleted the ghosts, made the graph-only ids `OrphanSample` nodes and added `IN_PROJECT`; what remains is under
"Known issues" in "Measured".

## v1.1: the graph_search target

Built by the graph_search proof of concept (`docs/superpowers/specs/2026-09-14-graph-search-poc-design.md`). It keeps every v1.0 node and relationship except `CHILD_OF` and
the ghost nodes, and adds metadata, a catalog, people and projects.

### Nodes

| Label | Properties | Key | Source |
|---|---|---|---|
| `Sample` + `T_<code>` | system: `id`, `uuid`, `type`, `title`, `project_ids` (sorted distinct list of ints), `search_text`, `synced_at`; metadata: every non-empty attribute, named exactly by its title | `id` (unique) | `samples`, `projects_samples` |
| `SampleType` | `id`, `title`, `label`, `uuid`, `seek_description`, `deprecated`, `sample_count`, `attribute_count`, `has_context`, and from the context table when present: `name`, `summary`, `tags`, `curated_parents`, `curated_children` (delimited strings), `clade` | `id` (unique); `title` and `label` unique | `sample_types`, `dmac.sample_types_context`, `dmac.sample_types_clades` |
| `Attribute` | `key`, `id`, `sample_type_id`, `sample_type`, `title`, `pos`, `required`, `is_title`, `base_type`, `value_type`, `declared`, `seek_description`, `meaning`, `role`, `unit_key`, `needs_backticks`, `sample_count` | `key` = `"<sample_type_id>:<title>"` (unique); `id` unique where present | `sample_attributes`, `sample_attribute_types`, `dmac.sample_attributes_unique` |
| `Project` | `id`, `title` | `id` (unique) | `projects` |
| `Person` | `id` only; no name or email | `id` (unique) | `people` |
| `Study`, `Investigation` | as v1.0; `Investigation.project_id` is now written; SEEK studies added by the writer carry `seek_study_id` | `Study.id`, `Study.seek_study_id`, `Investigation.id` | `studies`, `investigations`, `investigations_projects` |
| `GraphMeta` | `schema_version` (`"1.1"`), `catalog_hash`, `synced_at` | single node | the writer |
| `OrphanSample` | a former `Sample` with no row in MySQL; properties and edges kept | `id` | relabeled by the writer |

### Relationships

| Pattern | Source |
|---|---|
| `(:Sample)-[:DERIVED_FROM]->(:Sample)` child to parent | parent tokens in `json_metadata` (unchanged rule) |
| `(:Sample)-[:OF_TYPE]->(:SampleType)` every sample | `samples.sample_type_id` |
| `(:SampleType)-[:HAS_ATTRIBUTE]->(:Attribute)` | `sample_attributes`, plus observed undeclared keys |
| `(:Sample)-[:IN_PROJECT]->(:Project)` | distinct `projects_samples` pairs |
| `(:Person)-[:MEMBER_OF {has_left, time_left_at}]->(:Project)` | `group_memberships` joined to `work_groups` |
| `(:Investigation)-[:IN_PROJECT]->(:Project)` | `investigations_projects` |
| `(:Sample)-[:IN_STUDY]->(:Study)-[:IN_INVESTIGATION]->(:Investigation)` | unchanged |

Removed: `CHILD_OF`, and any DERIVED_FROM edge between two `Sample` nodes that MySQL's parent tokens do not declare
(both archived to a file before deletion).

### Rules

1. **Metadata property names are attribute titles, verbatim.** Case, spaces, punctuation and trailing spaces are kept;
   names outside `[A-Za-z0-9_]` need backticks in Cypher. The metadata key `UID` is not written (it equals `uuid`).
   `Type` and `ID` are metadata and differ from the system properties `type` and `id`.
2. **Empty is absent.** A value that is `null`, `""`, an empty list or an empty map is not stored, so `IS NOT NULL`
   means "has a value". Never test for `''`.
3. **Types follow SEEK's declaration.** `value_type` is `float`, `integer`, `date` or `string`, derived per (type,
   title) from `sample_attribute_types.base_type`. A value that parses is stored typed; one that fails keeps its raw
   string. A range comparison therefore sees only the typed values.
4. **Labels.** Every Sample has exactly one type label, `T_` plus the title with each character outside
   `[A-Za-z0-9_]` replaced by `_` (TIS becomes `T_TIS`, D.SEQ becomes `T_D_SEQ`). `SampleType.label` stores it.
5. **Scope is server-side.** A non-admin reader is limited by `any(p IN s.project_ids WHERE p IN $projects)` with
   `$projects` from MySQL membership. Readers never let a caller supply it.
6. **Keyword search** uses the fulltext index `sample_search_text` on `Sample.search_text`, which holds every
   non-empty value (never a key name), one per line.
7. **Page in the database.** `ORDER BY s.id SKIP $skip LIMIT $limit`, with a separate count.
8. **The catalog describes what a type may carry.** Every property on a `T_X` node (system properties excepted) is
   the `title` of an `Attribute` on SampleType X. `declared: false` marks a key found in data but not declared in SEEK.
9. **Lineage** keys (any title containing `Parent`) keep their text value and also produce DERIVED_FROM edges.
10. **Orphans** carry `OrphanSample`, not `Sample`, so no search sees them.

### Constraints and indexes

- Uniqueness: `Sample.id`, `SampleType.id`, `SampleType.title`, `SampleType.label`, `Attribute.key`, `Attribute.id`,
  `Project.id`, `Person.id`, `Study.id`, `Investigation.id`.
- Range: `Sample.uuid` (not unique while MySQL holds duplicate uuids), `Sample.type`, and per-type metadata indexes
  within an index budget: numeric and date attributes with values, string attributes on at least 1,000 samples with
  no value over 4,000 characters, and named benchmark keys. Never a lineage or file key: a range-indexed value over
  about 8 KB fails the whole write transaction.
- Fulltext: `sample_search_text` on `Sample.search_text`.
- Neo4j Community rejects existence and type constraints, so "every Sample has `project_ids`" and "every property has
  an Attribute" are enforced by the writer's verification, not the database.

### Versioning

`GraphMeta.schema_version` names the version a graph was written to. A change to any table above bumps the version
here and in the writer in the same commit.

## v1.2: what the sync adds

Built by the graph_sync follow-up (`docs/superpowers/specs/2026-09-15-graph-search-sync-design.md`, sections 6, 7.3
and 9), so that graph_sync can keep the graph equal
to MySQL by itself. Everything in v1.1 holds unless this section changes it. A graph becomes 1.2 only through a full
sync at 1.2; the sync's by-id paths write nothing to a graph at any other version.

### Nodes

| Label | Change from v1.1 |
|---|---|
| `Sample` | new system property `source_hash`: a sha256 hex digest of everything the node is projected from (the uuid, the title, the type's title and its attribute value types, the raw `json_metadata` bytes, the sorted project ids and the sorted assay ids). A node whose hash differs from the one computed from MySQL is synced again; a node written by anything else simply mismatches |
| `Sample` | `parent_titles` and `parent_title_hashes` are projection-owned: every graph_sync write computes them from the parent tokens with batch upload's rule (`enrich_parent_titles`), so orphan discovery keeps finding new uploads. A write that does not carry them keeps the node's own |
| `GraphMeta` | `schema_version` is `"1.2"`; new `label_maps_hash`, a digest of the resolved assay map and of `sops` (id, title), so a change to either is found without reading every edge. A write that does not name it keeps it |
| `OrphanSample` | never carries a `T_` label, `OF_TYPE` or `IN_PROJECT` (see "The deletion rule"); `orphaned_at` records when it became one. An orphan made before 1.2 is left as it is, so it may keep `OF_TYPE` and `IN_STUDY` and lack `orphaned_at` |

### DERIVED_FROM labels

graph_sync labels every DERIVED_FROM edge between two Sample nodes with seven properties:

| Property | Holds |
|---|---|
| `assay_id` | the SEEK assay of the winning shared assay |
| `internal_assay_id`, `internal_assay_title` | the winner: among the assays both endpoints share in `assay_assets`, resolved through `dmac.assays_internal_assays` to `dmac.internal_assays`, the smallest internal id; a SEEK assay with no mapping falls back to its own id and title |
| `internal_assay_ids`, `internal_assay_titles` | the plural lists, beside the singular fields |
| `protocol_id`, `protocol_title` | the child's stored `Protocol` value through the house three-format rule (`nextseek_api/batch_upload/helpers.py`), resolved to `sops`; a title that names several SOPs gives null |

Rules:

1. **Batch upload's rule, fed from MySQL.** The labels equal what batch upload computes from MySQL for the same
   edge; any difference is reported, never changed silently.
2. **All seven together, every time.** A write sets every property, nulls and empty lists included, never a subset.
   The legacy `assay_title` is removed from every edge graph_sync labels.
3. **Label on create.** Every edge graph_sync creates is labelled in the same run, so a graph rebuilt from an empty
   Neo4j stays labelled.
4. **Only new labels without the operator's approval.** By default a label is written only on an edge whose three
   singular assay fields (`assay_id`, `internal_assay_id`, `internal_assay_title`) are all null, and the write
   statement itself checks that, so a label written between a read and the write is kept. Every other difference is
   classified per edge (`new`, `equal`, `plural_missing`, `changed`, `cleared`) and reported per property; it is
   written only with the operator's opt-in (`--apply-label-changes` for one command run,
   `NEXTSEEK_GRAPH_SYNC_LABEL_CHANGES=apply` for the loop), and then only where all seven stored values still equal
   those read.
5. **A missing plural list is not a difference to write.** On an edge whose singular fields match the rule, absent
   `internal_assay_ids` and `internal_assay_titles` are reported as `plural_missing` and written only with the same
   opt-in.

A sync by sample id also applies the lineage rule of v1.1 to those samples as children: a declared pair missing
from the graph is created, and an undeclared DERIVED_FROM from one of them to a Sample parent is archived to the
run's `derived_from_undeclared_archive.tsv` (appended) before it is deleted.

### The deletion rule

A sample that left MySQL ends in one state, whichever path removes it (the legacy delete, the SEEK proxy delete, a
by-id sync, the nightly or the weekly sync):

- a `Sample` that graph_sync wrote (it carries `synced_at`) mirrors a row that is gone: its id, uuid, type and
  incident-edge count are appended to the run's `retired.tsv`, then it is `DETACH DELETE`d;
- a `Sample` that graph_sync never wrote (no `synced_at`: a v1.0 graph-only node, which may carry lineage MySQL never
  had) becomes an `OrphanSample`: `Sample`, every `T_` label, `OF_TYPE` and `IN_PROJECT` are removed, `orphaned_at`
  is set, its properties and DERIVED_FROM are kept;
- an existing `OrphanSample` is left as it is. A ghost (a second node of a live id) is still deleted by the full
  sync.

The line is exact because only graph_sync sets `synced_at`. With no `T_` label left on an orphan, a reader that
starts from `MATCH (s:T_X)` never sees one.

### Constraints and indexes

Unchanged from v1.1.

### Versioning

`GraphMeta.schema_version` reads `"1.2"`. `catalog_hash` is computed as in v1.1, so a reader that needs the v1.1
catalog should accept any version from 1.1 up rather than exactly `"1.1"`.
