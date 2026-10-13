# Graph schema 1.3: assay nodes

Status: design, revision 4 (2026-09-29): one Assay per internal assay; every write path; the adversarial review of
origin/dev 926be1e3 folded in (revision 3); rebased onto the graph program's release order (revision 4). Tracking:
this spec itself, tracked in the repository; no issue is filed for this work. Base: the graph program's working branch
after release 1 (the graph contract, `nextseek_graph/schema.py`) and release 2 (the studies release and the studies
tool). Mock-up: the "Graph Assay Nodes" artifact.

**Revision 4.** Graph schema 1.3 now ships third, after the graph contract and the studies release, and this revision
applies the two rebase lists those releases' specs carry: the graph contract spec's section 10.2 ("Graph schema 1.3")
and the studies release spec's section 14 ("1.3 plan tasks to rebase after this release"), both working documents of
the graph program outside the repository. What changed:

- The version and the names (section 4.6, 5.4): 1.3's label, relationship types, properties, constraint and index
  enter the graph contract as its `_V13` groups in the commit that writes the v1.3 tables into
  `docs/neo4j-schema.md`; `SCHEMA_VERSION` moves to `"1.3"` in the contract, not in `writer.py` (whose name is an
  alias of it), in the later commit that writes the v1.3 Versioning subsection. Consumers read the groups they name.
- Studies (sections 1, 5.5, 5.6, 5.7, 12): the studies release made IN_STUDY follow SEEK, rebuilt from sample to
  assay to study, merged each box's split Study nodes, gave SEEK Study nodes SEEK's description, and made the assay
  proxy enqueue the members an assay links, unlinks or moves. So the IN_STUDY known issue and its "Not in scope"
  mention are gone, RUN_IN reaches the merged Study node by `seek_study_id` with nothing to move, a Study node 1.3
  writes carries SEEK's title, description and investigation, and D11 cites the studies release's proxy rows.
- The gate G family is `13.assays` (section 5.9, 8): `12.studies` is the studies release's.
- The doc-parsing tests (section 7, 8) are superseded by the graph contract's test, which reads every `## vX.Y`
  section of `docs/neo4j-schema.md` against the contract's groups.
- Batch upload's old graph module, neo4j_sync, is gone (release 1 deleted it and rewrote the docs that named it),
  so the last known issue and the batch upload guide's reporter line leave this spec.
- The rollout (section 10) follows the program's two-stage push and runs no study merge and no study step.

## 1. Goal

Give every assay a node in the Neo4j graph, so a question about assays (which samples went into or came out of one,
which studies ran it, which sample types it takes and makes) is a direct graph read. Lineage keeps working exactly as
it does at 1.2.

Not in scope: Protocol nodes; SEEK assay runs as nodes; paper-level Study links for assays; the Study layer and
IN_STUDY, which the studies release made follow SEEK before this release (a sample's IN_STUDY is rebuilt from its
assays' studies on each sync, and the nightly reconcile finds an assay moved in SEEK's UI).

## 2. Decisions

| # | Decision | Source |
|---|---|---|
| D1 | `DERIVED_FROM` stays Sample to Sample with all nine properties, unchanged, and is the only lineage. | measured (section 3); operator |
| D2 | One new label, `Assay`: one node per row of `dmac.internal_assays`. SEEK assays (runs) are not nodes; their ids ride on edges. | operator |
| D3 | Samples attach with `(:Sample)-[:INPUT_TO]->(:Assay)` and `(:Sample)-[:OUTPUT_OF]->(:Assay)`, both pointing at the Assay, each carrying the SEEK assay ids it came from. The role is computed from lineage (section 5.3), because `assay_assets.direction` is unread and filled three ways. | operator; spec for the role source |
| D4 | Sample types attach through the curated catalog only (`dmac.assay_context`): `(:SampleType)-[:ACCEPTED_BY {required, group}]->(:Assay)` and `(:Assay)-[:GENERATES]->(:SampleType)`. The Assay node also lists the codes (`input_types`, `optional_input_types`, `output_types`). | operator (catalog only, distinct names); review (lists) |
| D5 | An Assay links to the SEEK studies its runs belong to: `(:Assay)-[:RUN_IN {seek_assay_ids}]->(:Study)`, to Study nodes keyed by `seek_study_id`, never the paper-level studies. A Study node the studies release merged carries `seek_study_id` too, so RUN_IN finds it by that key; every box is merged before 1.3 reaches it, so nothing moves RUN_IN. | operator (link, distinct name); studies release |
| D6 | Three guards keep lineage off the Assay (section 6.3): both sample edges point at it, the structure text says so, and the query-shape guard refuses any query that pairs samples through one Assay. | operator |
| D7 | The Assay node holds catalog facts only, the same for every caller. Run ids live only on edges. A non-admin reads the Assay and walks INPUT_TO and OUTPUT_OF from samples they can see; never ACCEPTED_BY, GENERATES (their SampleType end stays refused, as ruled on 2026-09-18) or RUN_IN. | review; scope spec S4 |
| D8 | The graph agent is told about the Assay only on a graph at 1.3 or later: all new agent text lives in one file appended by version. | spec |
| D9 | A SEEK assay with no internal mapping makes no sample edge; drift reports it with its member count for the curators. | spec |
| D10 | The sample edges are rewritten for the touched samples and their lineage partners, read both before and after the lineage step, and for the partners of a retired sample before it is deleted (section 5.5). | spec; review |
| D11 | The assay proxy already enqueues `samples` rows for the members of an assay it creates, or whose `relationships.samples` or `relationships.study` a PATCH sets, the members before the PATCH included, one row per `SAMPLE_CHUNK` (the studies release, its decision S10). 1.3 adds nothing to the proxy: the `samples` drain runs `sync_samples`, which at 1.3 also rewrites those samples' INPUT_TO and OUTPUT_OF. | spec; review; studies release |
| D12 | The sample edges are derived and owned by graph_sync: a change is always written, with no opt-in. DERIVED_FROM labels keep their rule 4 opt-in. | spec |
| D13 | Batch upload needs no change of its own: its stage 6 already calls `targeted.sync_samples`. | spec |
| D14 | No new outbox kind: the `assay_map` and `isa` drains run `sync_assays`. | review |

## 3. Why lineage never runs through an Assay

Measured on the local graph (schema 1.2, read only, 2026-09-25). One assay takes many parents and makes many
children, so a node in the middle of a lineage path forgets which child came from which parent.

| Lineage read through | Real pairs | Pairs it would read |
|---|---:|---:|
| one node per internal assay (this Assay) | 1,998,154 | 46,206,841,781 |
| one node per SEEK assay run | 1,998,154 | 2,461,481,902 |

Nothing of that size is stored: the graph holds one edge per sample, role and assay. The number is what a query would
count if it paired every output with every input of one Assay. Section 6.3 refuses that query.

## 4. Schema 1.3

Everything in 1.2 holds unless this section changes it.

### 4.1 The Assay node

| Property | Holds | Source |
|---|---|---|
| `id` | the internal assay's id; unique | `dmac.internal_assays.id` |
| `title` | `internal_assay_title`, the name DERIVED_FROM labels carry | `dmac.internal_assays` |
| `other_names` | list: `alternative_assay_names` split, plus `assay_name` when it differs from `title` | `dmac.assay_context` |
| `description`, `tags` | the catalog text | `dmac.assay_context` |
| `parent_clade`, `child_clade` | `parent_clade_type`, `child_clade_type` | `dmac.assay_context` |
| `input_types`, `optional_input_types`, `output_types` | sample type codes, flattened from the parsed groups | `dmac.assay_context` |
| `has_context` | true when a catalog row is linked | derived |

Empty is absent. No property holds run ids, counts or anything read per project.

The catalog is read softly: an absent `assay_context` table gives nodes with `has_context: false` and no catalog
edges; columns are lowercased on read (`services/context_catalog._rows_from_cursor`). A catalog row with no
`internal_assay_id` makes nothing. Several rows linked to one internal assay: the lowest row id wins. An internal
assay title held by two ids makes two nodes. Every one of these is reported.

### 4.2 Relationships

| Pattern | Properties | Source |
|---|---|---|
| `(:Sample)-[:INPUT_TO]->(:Assay)` | `seek_assay_ids`: the runs of this kind in which the sample was an input | lineage and `assay_assets`, section 5.3 |
| `(:Sample)-[:OUTPUT_OF]->(:Assay)` | `seek_assay_ids`: the runs in which it was an output | the same |
| `(:Assay)-[:RUN_IN]->(:Study)` | `seek_assay_ids`: the runs of this kind in that study | `assays.study_id`, through `assays_internal_assays` |
| `(:SampleType)-[:ACCEPTED_BY]->(:Assay)` | `required` (true or false) and `group` (the index of the either-or group, so "TIS or CEX or CEL" is one choice) | the parent columns, parsed with `parse_alternation` |
| `(:Assay)-[:GENERATES]->(:SampleType)` | `group` | `children_sample_types`, parsed the same way |

All five types are new; no existing type gains a new label pair. `parse_alternation` drops a code it does not know,
so a separate pass reports those codes. A mapping row with a NULL `internal_assay_id`, an id missing from
`internal_assays`, or a SEEK id missing from `assays` is dropped and reported. A study with no Study node gets one
(section 5.6).

### 4.3 DERIVED_FROM, unchanged, and how it meets the Assay

- "Children that underwent X" (the child of an edge inside X) stays the edge test the prompt teaches today:
  `r.internal_assay_title = $assay OR $assay IN coalesce(r.internal_assay_titles, [])`.
- "Samples that went into or came out of X", "which assays", "which studies ran X" and every type-level question go to
  the Assay (section 6.1 says which is which).
- `internal_assay_id` and every entry of `internal_assay_ids` name an Assay id only when that entry's SEEK assay is
  mapped; an unmapped entry holds the SEEK id and title (`graph_sync/labels.py`), and a SEEK id can equal some Assay's
  id. A join from an edge to an Assay therefore matches on id and title together. After an internal assay is renamed,
  the edge labels keep the old title until the operator approves the relabel (rule 4), so such joins miss those edges
  in between; drift counts them.

2026-09-30: renames relabel without approval (the studies release, its Amendment A12). The edge labels keep the old
title until the relabel reaches them (seconds after a rename saved in the admin; a rename writes without approval, v1.2
rule 4 as narrowed), so such joins can miss those edges in between; drift counts them.

### 4.4 Constraints and indexes

A uniqueness constraint `assay_id_unique` on `Assay.id` and a range index `assay_title` on `Assay.title` (never a
`gs_*` name, which the index budget drops), in the constraint list the full sync applies and gate G checks.

### 4.5 Deletion

- An Assay whose id left `internal_assays` is `DETACH DELETE`d, its edges first, in batches.
- `RUN_IN`, `ACCEPTED_BY` and `GENERATES` are replaced whole on each write; every delete names both labels.
- A Sample's `INPUT_TO` and `OUTPUT_OF` edges are replaced whole whenever they are rewritten (section 5.5).
- `ORPHAN_SWAP` (a Sample becoming an OrphanSample) also drops its INPUT_TO and OUTPUT_OF.

### 4.6 Versioning

`GraphMeta.schema_version` reads `"1.3"`. A graph becomes 1.3 only through a full sync at 1.3; below it, the loop
claims no rows but drift, as at 1.2. `catalog_hash` keeps its definition; GraphMeta gains no key.

The names of sections 4.1, 4.2 and 4.4 enter the graph contract (`nextseek_graph/schema.py`) as its 1.3 groups
(`ASSAY`, `LABELS_V13`, the five relationship constants, `RELATIONSHIPS_V13`, `RELATIONSHIP_PATTERNS_V13`,
`NODE_PROPERTIES_V13`, `UNIQUE_CONSTRAINTS_V13`, `RANGE_INDEXES_V13`) with `"1.3"` appended to `VERSIONS`, in the
commit that writes the v1.3 tables into `docs/neo4j-schema.md`, before any code reads them. The contract's
`SCHEMA_VERSION` becomes `"1.3"` in a later commit, the one that writes the v1.3 Versioning subsection; `writer.py`'s
`SCHEMA_VERSION` is an alias of it and follows. Until then the section is being built and the writer does not write
it (the contract's versioning rule).

## 5. Writer (graph_sync)

### 5.1 Sources (`sources.py`)

- `internal_assays()`: `SELECT id, internal_assay_title FROM internal_assays` (dmac).
- `assay_internal_pairs()`: every `(assay_id, internal_assay_id)` row of `assays_internal_assays` with `assay_id`
  set; a row with a NULL `internal_assay_id` is kept so `assays.build_catalog` can report and drop it. The existing
  `internal_assay_links()` keeps only the smallest per assay, for the label rule, and stays as it is.
- `assay_studies()`: every SEEK assay with its study, `assays` left-joined to `studies`, so an assay whose study row
  is gone reads as having no study.
- `sample_ids_in_assays(assay_ids)`, the members of given SEEK assays, is the studies release's reader, used here by
  `sync_assays` step 4.
- `assay_context_rows()`: `SELECT * FROM assay_context` behind `table_exists`, columns lowercased.
- Memberships reuse `sample_assay_ids_for` and `_ASSAY_LINK_STREAM_SQL`.

### 5.2 A pure module (`assays.py`)

No I/O, like `catalog.py` and `labels.py`. It builds the Assay property maps, the `RUN_IN`, `ACCEPTED_BY` and
`GENERATES` rows, the role rows of section 5.3, and the reports (catalog rows with no id or duplicated, unknown codes,
bad mapping rows, duplicate titles, unmapped SEEK assays with members, members with no role).

### 5.3 The role rule

For each DERIVED_FROM pair (child c, parent p) between two Samples, take the SEEK assays both hold in `assay_assets`
and map each through every row of `assays_internal_assays`. For each shared SEEK assay s mapped to internal assay a:
c gets `OUTPUT_OF` a with s in `seek_assay_ids`, and p gets `INPUT_TO` a with s in `seek_assay_ids`. A sample can be
both, in one run or in several; a same-type edge (A.VCF to A.VCF) gives each end its own role. OrphanSample ends are
skipped. A member with no lineage edge inside a run gets no edge for it; the sync counts these.

### 5.4 Statements and writer functions

`cypher.py` gains `MERGE_ASSAYS` (`MERGE (a:Assay {id}) SET a = r`), `DELETE_GONE_ASSAYS` (batched),
`REPLACE_ASSAY_RUNS`, `REPLACE_ASSAY_CATALOG_EDGES`, and `REPLACE_SAMPLE_ASSAY_EDGES`: one statement per chunk of
samples that deletes their `(:Sample)-[:INPUT_TO|OUTPUT_OF]->(:Assay)` edges and creates the new ones. `writer.py`
gains one function per statement, chunked like the sample writes; the version moves in the contract (4.6). The
constraint and index statements are rendered by `cypher.py`'s DDL templates from the contract's 1.3 groups, and come
out byte for byte as the literal statements the writer tests pin. Python that names the Assay label or one of the five
relationship types reads the contract; the Cypher text keeps its literal names.

### 5.5 Every write path

Nothing creates a SEEK assay together with its internal mapping: a new SEEK assay is created first (API or SEEK's
UI), and mapped later by hand (the internal-assay admin's "Sync Assays", then an association, or context_gen SQL).

| Event | Where | At 1.3 | Reaches the graph |
|---|---|---|---|
| New or updated samples, batch upload | WR-01 to WR-03; stage 6 calls `targeted.sync_samples` inline | the same call rewrites the sample edges of the touched samples and their partners (below) | end of job, as today |
| New samples, other writers | sample proxy (WR-07), legacy upload (WR-12), assay registration (WR-11), orphan resolution (WR-04) | the `samples` drain does the same | about 5 s |
| Samples linked to an assay through the API | assay proxy `create`, `partial_update` (WR-09) | the studies release's rows (D11): the members on a create and on a PATCH that sets `relationships.samples` or `relationships.study`, the members before the PATCH included (read from `assay_assets` before the call), one `samples` row per `SAMPLE_CHUNK`, keys `batch:assay:<id>:<time_ns>:<n>`. Their `sync_samples` rewrites the sample edges too; the proxy gains no code | about 5 s |
| Retired samples | every retire path (`targeted.retire_samples`, the reconcile, gone ids in `_sync_ids`) | the partners of each retired sample are read before the delete and rewritten after it | with the retire |
| New SEEK assay | assay proxy (WR-09); SEEK's UI (WR-22); never batch upload | no Assay change until mapped; drift reports it with its member count | at mapping time |
| SEEK assay mapped, remapped, unmapped or deleted | internal-assay admin (WR-15): `assay_map`; context_gen SQL (WR-21): none | the `assay_map` drain runs `sync_assays` | admin: about 5 s; context_gen: nightly |
| New, renamed or deleted internal assay | internal-assay admin (WR-15): `assay_map` | `sync_assays` | about 5 s |
| Assay moved to another study | assay proxy (WR-09): `isa`, and the studies release's member rows; SEEK's UI: none | the members' IN_STUDY follows as the studies release made it (their `samples` rows, and the nightly reconcile's `study_links` step); 1.3 adds only RUN_IN: the `isa` drain runs `sync_assays`, which replaces it | proxy: about 5 s; UI: nightly |
| Catalog edit (`assay_context`) | context_gen SQL the operator applies; the installer's seeds | `assay_context` joins the graph source tables; the reconcile and `catalog_sync` write the catalog | nightly, or `graph_sync --catalog` |

**Partners (D10).** A sample's partners are the other ends of its DERIVED_FROM edges. `sync_samples` reads the
touched samples' partners before its lineage step (which deletes undeclared edges) and after it, and rewrites the
sample edges of the touched samples and of both partner sets. Rewriting one sample reads all of its own DERIVED_FROM
edges and the `assay_assets` rows of all of its partners: one hop of writes, two hops of reads. A partner with more
than `PARTNER_REWRITE_MAX` edges (set by the plan from the measured maximum degree) is not rewritten inline; it gets
its own `samples` row, so a hub parent never holds stage 6's graph lock.

### 5.6 `sync_assays`

Run by the `assay_map` and `isa` drains, the nightly reconcile and the full sync:

1. Read `internal_assays`, `assays_internal_assays`, `assays.study_id` and `assay_context`.
2. Merge the Assay nodes' catalog properties (never deleting yet).
3. Find the SEEK assays whose mapping changed. A `(seek id, Assay id)` pair in MySQL but not in RUN_IN is added; a
   pair in RUN_IN or on the sample edges (`UNWIND r.seek_assay_ids` over INPUT_TO and OUTPUT_OF) but not in MySQL is
   removed; a SEEK assay deleted from SEEK is marked by its pairs in the graph. RUN_IN is the baseline for added pairs
   because it is replaced only in step 5, after the members are rewritten: the sample edges cannot be, since a mapped
   run whose members hold no role (no lineage inside it) never puts its pair on a sample edge and would be marked on
   every call, and a crash after the first chunk would hide the rest from the retry. A mapped SEEK assay with no
   study has no RUN_IN row; it falls back to the sample edges.
4. Rewrite the sample edges of every member of the marked SEEK assays: members in `assay_assets` plus samples whose
   edges carry the id. One write unit per chunk, like `sync_samples_of_type`. Above `ASSAY_REWRITE_MAX` members, a
   `full` slot is enqueued instead, as the reconcile does.
5. Replace RUN_IN, merging `Study {seek_study_id}` for any study with no node through the studies release's
   `write_seek_study_nodes`, from SEEK's whole study row (title, description, investigation), so a Study node this
   step touches ends as the studies release writes it. `assay_studies()` joins `studies`, so an assay whose study row
   is gone gets no RUN_IN, as its members get no IN_STUDY. RUN_IN finds a merged Study node by `seek_study_id` (D5).
6. Replace ACCEPTED_BY and GENERATES.
7. Delete the Assays gone from `internal_assays`, in batches.

`catalog_sync` runs steps 1, 2, 6 and 7 only: it is called inside `sync_samples` (for a missing type or a new
undeclared attribute), where a member rewrite does not belong, and only a run that rewrote the members may replace
RUN_IN.

### 5.7 The full sync

- The constraint step applies `assay_id_unique` and `assay_title`.
- `label_edges` already streams every edge between two Samples with both ends' SEEK assays. The same stream emits one
  role code per (sample, SEEK assay, role), packed into an `array('q')` within the run's memory budget of about 8
  bytes a link; after the stream the array is sorted by sample.
- After `seek_studies` (the studies release's rebuild of the SEEK Study nodes and every sample's IN_STUDY, its
  removals gated by the box's switch): `sync_assays` steps 1, 2 and 6, then the sample edges from the sorted array in
  chunks of samples, then RUN_IN (step 5), then step 7. The full sync rewrites every sample's edges, so it skips steps 3 and 4.
  Before the first write it reads the ids of every Sample holding INPUT_TO or OUTPUT_OF, and gives each one the array
  does not name an empty row, so a sample that lost its role loses its edges.
- Old sample edges are deleted inside each chunk's own statement, so no transaction grows with the graph.

### 5.8 While a graph is below 1.3

Every write path refuses a graph at another version, and the loop claims no rows but drift until the full sync at 1.3
has run. Batch upload reports "graph: pending" and graph_search and chat answer from the old graph in between. The
rollout (section 10) keeps the gap short.

### 5.9 Checks

- Gate G gains the family `13.assays` (`12.studies` is the studies release's): Assay ids against `internal_assays`;
  RUN_IN against `assays.study_id`; the catalog edges against the parsed catalog; and, for a sample of samples, their
  complete INPUT_TO and OUTPUT_OF set (both roles, every Assay, every SEEK id) against the role rule, so a stale extra
  edge fails. `EXPECTED_CONSTRAINTS` and `EXPECTED_INDEXES` are the names of the contract's 1.1 and 1.3 constraint and
  index groups, plus the fulltext name. `8.graphmeta.schema_version` expects the writer's version, `"1.3"`. The fake in
  `test_graph_sync_verify.py` answers the new statements; the `gate_g` docstring's family list and check count
  follow.
- Drift adds the Assay id set in the `_check_catalog` shape, and reports, never fails on: unmapped SEEK assays with
  members, members with no role, and DERIVED_FROM edges whose labels disagree with the Assay layer after a rename.
- The writer registry: `GRAPH_SOURCE_TABLES` already holds `assays`, `assay_assets`, `assays_internal_assays` and
  `internal_assays`; `assay_context` joins it. The scan then finds new sites, declared under the existing entries:
  `startup/seed/sql/assay_context.curated.sql` and `assay_context.sql` (the installer), `scripts/context_gen.py`'s
  update SQL and `_RELINK_ASSAYS` (WR-21), `scripts/generate_assay_context_seed.py`.

## 6. Reader: the agents

### 6.1 What the graph agent reads (D8)

A new file `prompts/graph_schema_structure_assays.txt`, appended to the structure text by
`graph_context.render_graph_context` when the snapshot's `schema_version` is 1.3 or later, for the default prompt and
every prompt variant (the graph contract's `at_least(schema_version, "1.3")`). It carries all new agent text;
`graph_agent.txt` does not change. Proposed text, for the
operator's word-by-word review in the plan:

```
## Assays (schema 1.3)
(:Assay {id, title, other_names, description, tags, input_types, optional_input_types, output_types,
  parent_clade, child_clade})   one per assay kind, the same for every caller
(:Sample)-[:INPUT_TO {seek_assay_ids}]->(:Assay)    the sample went into the assay
(:Sample)-[:OUTPUT_OF {seek_assay_ids}]->(:Assay)   the sample came out of it
11. Lineage is DERIVED_FROM only. Two samples on one Assay did not come from each other: never pair samples through
   an Assay. "Went into / came out of / used in X" and "which assays": use the Assay, matching
   toLower(a.title) = toLower($x) OR toLower($x) IN [n IN a.other_names | toLower(n)]. Went through X:
   WHERE EXISTS { (s)-[:INPUT_TO]->(a) } OR EXISTS { (s)-[:OUTPUT_OF]->(a) }. Studies that ran X: the IN_STUDY of
   those samples. "Underwent / processed via X" stays the DERIVED_FROM edge test of STEP 4.
```

The admin-only relationships (ACCEPTED_BY, GENERATES, RUN_IN) are not in this text; the catalog lists on the node
answer "which types does X take or make" for every caller. The size test gains a 1,024-byte cap on the new file (the
text above is under it); the main file keeps its 5,400-byte cap, of which the studies release leaves it at most
5,300 bytes, and this release adds nothing to it. The rendered context of a heavy question is measured again on the
studies release's base. The structure file's first line and
`SCHEMA_HEADING`, on both the catalog and the fallback path, name the graph's version.

### 6.2 Guards (`agents/graph.py`)

- `V11_NODE_PROPERTIES` gains `Assay` with the properties of section 4.1, from the contract's `NODE_PROPERTIES_V13`.
- `V11_RELATIONSHIP_PROPERTIES` gains `INPUT_TO {seek_assay_ids}`, `OUTPUT_OF {seek_assay_ids}`,
  `ACCEPTED_BY {required, group}`, `GENERATES {group}` and `RUN_IN {seek_assay_ids}`, from `RELATIONSHIPS_V13`.
- `_SAMPLE_SOURCE_RELATIONSHIPS` treats the source of every `IN_STUDY` and `OF_TYPE` edge as a Sample. With distinct
  names no Assay starts one, but the rule becomes label-aware so an Assay-labelled source is never a sample. It and
  the new set of labels that are never a Sample are policy sets: spelled with contract constants, never derived from
  a contract group, and pinned by literals.
- `graph_review._zero_unproven_base` adds `Assay` to the catalog labels whose zero is not a sample zero.

### 6.3 The three guards against false lineage (D6)

1. **Direction.** INPUT_TO and OUTPUT_OF both point from the sample to the Assay.
2. **Words.** Rule 11 (section 6.1).
3. **Refusal.** `query_shape_problems` gains `assay_join`: a statement is refused when, in one row scope, two
   distinct sample variables reach the same Assay variable through INPUT_TO or OUTPUT_OF, in any of these forms: one
   pattern; comma-joined parts; separate MATCH or OPTIONAL MATCH clauses; a variable carried by WITH; an undirected
   or alternated relationship; a variable-length or untyped path from a sample to an Assay; `shortestPath`. A
   COUNT or EXISTS subquery or a pattern comprehension whose sample variable is local to it makes no pairs and
   passes. `_shape_lines` and `_shape_refusal_parts` gain the `assay_join` wording: lineage is DERIVED_FROM.

### 6.4 Scope (`cypher_scope.py`, spec 2026-09-18 §5.4)

| Pattern | Rule for a non-admin |
|---|---|
| `Assay` | a new catalog kind: no predicate; every property readable (they are catalog facts) |
| `INPUT_TO`, `OUTPUT_OF` | fixed length, from a node of sample kind, which carries the sample predicate |
| `ACCEPTED_BY`, `GENERATES`, `RUN_IN` | refused, `relationship_type` (their SampleType or cross-project Study end) |
| `SampleType` | refused, as today (S4) |

The prover records each bound name's kind: a reference to an Assay proves no joined label, so
`MATCH (a:Assay) WITH a MATCH (a)-[...]->(st:Study)` cannot prove `st`. Admins are unscoped, as today. The prover's
label and relationship sets are policy: the Assay label is the contract's constant, and the widened fixed
relationships are spelled with contract constants and pinned by literals.

### 6.5 What does not change

The vocabulary; the parser's `min_graph_schema.json` (routing only); graph_search; `helpers/query_scope.py`;
Container-CC, which reads the same renderer through `nextseek-graph-schema`. The committed fallback picks the new
label up by rerunning `scripts/graph_schema_fallback.py` against a 1.3 graph.

## 7. Documents

- `docs/neo4j-schema.md`: a `## v1.3: assay nodes` section in the v1.2 section's shape (a Nodes table and a
  Relationships table, each with a `Properties` column, The role rule, DERIVED_FROM and the Assay, Deletion,
  Constraints and indexes with each name in parentheses after its `Label.prop`, Versioning, Rolling back); the intro
  names v1.3. The section lands in two commits (4.6): everything but Versioning and Rolling back with the contract's
  1.3 groups, then those two with the version. The graph contract's test reads every `## vX.Y` section against the
  contract's groups of that version, so no test of this release parses the doc's tables; it reads the new section
  with no change.
- `nextseek_api/graph_sync/README.md` (version, modules, outbox table, sections), `nextseek_api/README.md`,
  `nextseek_api/batch_upload/CLAUDE.md` (its "a graph failure does not fail the job" landmine names the writer's
  version instead of 1.2), `capabilities.md` (the graph section names the Assay; it is baked into the cc-agent image).
- The spec is tracked through a `.gitignore` exception and a `docs/INDEX.md` row once code cites it, with no issue
  number.

## 8. Tests

Every pin that moves: the full-sync step order; the constraint set; `test_graph_sync_command.py`, `_verify.py`,
`_drift.py`, `_sources.py`; the writer registry gate; `GRAPH_FILES` in `test_default_prompt_set.py` and
`test_default_prompt_content.py`; the scope tables in `test_cypher_scope_accept.py` and the refusal battery; the
equality pins of the composed names that now read 1.3 groups (the guard's two maps, `verify.EXPECTED_*`,
`FIXED_RELATIONSHIPS`). The version literals and the fakes that meant "the writer's version" read the contract since
release 1, so the version bump moves them with no edit, and the smoke check compares the graph with the box's own
writer version. The graph contract's test reads the v1.3 section and, from the version bump on, finds it at the
writer's version; its structure group gains the Assay file at version 1.3.

New, as unit tests unless named:
- `assays.py`: building; the role rule on hand-built lineage (multi-parent children, same-type edges, several runs of
  one kind, OrphanSample ends); the catalog's groups, duplicates, unknown codes, absent table and mixed-case columns;
  bad mapping rows; the reports.
- writer statements; the full-sync role step (position, and memory on a synthetic million-link stream).
- `sync_assays`: a new mapping for an assay that wins no edge; a deleted SEEK assay; a crash between steps and the
  retry; the `ASSAY_REWRITE_MAX` guard; `catalog_sync` inside `sync_samples` leaving members alone.
- the partners: a two-batch upload whose parent is in the first batch; an undeclared edge deleted by a Parent edit;
  a retired sample; the `PARTNER_REWRITE_MAX` guard.
- gate G `13.assays`, including a stale extra edge. (The assay proxy's enqueue is the studies release's, tested
  there.)
- `assay_join`: every refused form of section 6.3 and every passing one.
- scope: the Assay kind; each refused relationship; the WITH-carried reference; a non-admin count over an Assay's
  inputs; every Cypher example in the new prompt file passes `scope_cypher` for a non-admin. The Neo4j scope lane
  (`graph_scope/fixture_graph.py`, `test_scope_neo4j_lane.py`) gains Assay fixtures.
- the version-gated structure file on a 1.2 and a 1.3 snapshot.
- no live case: `ci/smoke/test_graph_behaviour.py` opens no Neo4j connection and no HTTP surface returns a sample's
  assay edges, and its version check already reads the box's writer version. On every box, gate G `13.assays` (run
  by the rollout after the full sync) checks the behaviour.

## 9. Measurements before the first full sync

On each box (local, dev, prod), read only: the row counts of `internal_assays` and `assay_context` and the
duplicates; mapping coverage; unmapped SEEK assays with members; members with no role; the expected sample-edge
count; the maximum DERIVED_FROM degree (sets `PARTNER_REWRITE_MAX`); the largest assay membership (sets
`ASSAY_REWRITE_MAX`). Local counts include TCGA; production has none.

## 10. Rollout

1. Build and review on the program's working branch, on top of releases 1 and 2. Pushes go in two stages to the
   program's own remote branch, never to dev directly: stage 1 holds releases 1 and 2, rolled out to every box with
   production's study migration finished; 1.3 is stage 2, pushed only after that, since the study merge and the
   studies tool refuse a graph not at the writer's version and 1.3 code stalls the loop on a 1.2 graph.
2. Rehearse the full sync at 1.3 in the throwaway lane at production's transaction limit (1g) on a copy of the local
   graph with the studies release applied (merged, its IN_STUDY rebuild run, its switch on), and time it. The
   2026-09-16 failure was the lane at 512m. Gate G expects `12.studies` and `13.assays` to pass.
3. Local: rebuild, full sync at 1.3, gate G and drift, regenerate the fallback.
4. Dev, then production, each with the full sync straight after the rebuild, inside the outbox freshness window
   (1 h), watching the box's memory. No study merge and no study step: every box ran them in release 2, its switch
   stays on, and the full sync's study step is the studies release's rebuild. Gate G and drift read `12.studies` too.
5. Re-key the nessie_tests assay families whose answers move from the edge test to the Assay (operator's call, since
   graded runs cost money).
6. Rolling back: a documented block (in `docs/neo4j-schema.md`) that deletes the Assay layer in batches and drops its
   constraint and index, then a 1.2 full sync.

## 11. Size

One Assay per `internal_assays` row: about 124 on today's local catalog (the measurement of section 9 fixes it). 210
ACCEPTED_BY (202 required, 8 optional) and 162 GENERATES edges from today's catalog. Sample edges: on the order of one
to two million, measured before the first sync.

## 12. Known issues this spec does not fix

- `assay_assets.direction` stays unread and inconsistent.
- An unmapped SEEK assay keeps its SEEK id in DERIVED_FROM's labels (section 4.3).
- Mapping a new SEEK assay is manual and two-step; until then its samples have no Assay edge.
- A child synced before its parent's node exists gets no edge until the nightly reconcile's new-parent pass.
