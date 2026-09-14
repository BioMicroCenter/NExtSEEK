# Neo4j graph schema

The document of record for the NExtSEEK sample graph: what it holds (v1.0), and what the graph_search work builds
(v1.1). Querying Neo4j over HTTP, Browser or bolt is in [`neo4j-programmatic-access.md`](neo4j-programmatic-access.md).
The auto-generated `neo4j_schema.json` files Nessie reads are caches, not this document.

Counts are dated measurements (2026-09-14) on the local production snapshot unless a row says otherwise; re-measure
before relying on one.

## v1.0: the graph as it is

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

## v1.1: the graph_search target

Built by the graph_search proof of concept (`docs/superpowers/specs/2026-09-14-graph-search-poc-design.md`, tracked
on the `feat/graph-search` branch until it merges). It keeps every v1.0 node and relationship except `CHILD_OF` and
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

Removed: `CHILD_OF` (archived before deletion).

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
