# `nextseek_api/graph_sync/`

## What this is

The writer for graph schema v1.1: it reads MySQL (SEEK's `seek` connection and the dmac `default` connection) and
writes the Neo4j sample graph that `graph_search` reads. Every sample gets its non-empty metadata as properties, a
`T_<code>` type label, `OF_TYPE`, `project_ids` and `IN_PROJECT`; the graph also gets the SampleType and Attribute
catalog, people, projects and one `GraphMeta` node. What the graph holds, and every rule the writer enforces, is
[`docs/neo4j-schema.md`](../../docs/neo4j-schema.md) section "v1.1". Why it exists and how it is proven is the
[design](../../docs/superpowers/specs/2026-09-14-graph-search-poc-design.md), section 6, and its
[plan](../../docs/superpowers/plans/2026-09-14-graph-search-poc.md).

It is built once, here, for the graph_search proof of concept; the later sync work schedules this same module rather
than writing a second projection.

## Surface

One entry point: the management command `graph_sync`.

```
manage.py graph_sync (--full | --catalog | --verify) [--json] [--dry-run] [--chunk N] [--run-dir PATH]
                     [--seed N] [--bench-keys FILE] [--i-mean-the-live-graph]
```

- `--full` runs the whole ordered sync (design section 6, "Order of a full run"; `run.py`'s module docstring). Its
  preflight writes nothing: it builds the catalog, projects every MySQL sample once and reads the ghost list, and
  refuses before the first write on any problem it finds.
- `--catalog` writes the catalog nodes only.
- `--verify` is read-only and runs gate G. `--seed N` fixes the seed of its random samples, so a run can be
  repeated; the seed used is in the result's `stats`.
- `--dry-run` makes `--full` and `--catalog` read MySQL and the graph, write nothing and print their counts.
- `--bench-keys FILE` (with `--full`) is a JSON list of attribute keys (`"<type id>:<title>"`) or
  `[sample type, attribute]` pairs that the index budget must cover. A lineage or file key, or one with a value over
  4,000 characters, is still never indexed.
- `--json` puts only the JSON result on stdout; progress lines go to stderr.

Exit status:

| Code | Means |
|---|---|
| 0 | success |
| 1 | `--verify` found a failing check, or a run failed part way (its report says where) |
| 2 | refused, and nothing was written: settings name no Neo4j URI; the host is the live stack's `neo4j` and `--i-mean-the-live-graph` is absent; or a sync's preflight found a problem (a sample that cannot be projected, a sample type SEEK does not have, a sample id on more than one node whose uuid is in MySQL, a SampleType title held under another id in the graph, a catalog that does not build, or no run directory) |

### What a full run writes

The run directory is `--run-dir`, else a new `graph_sync-<UTC time>` directory under `$GS_RUN_DIR`; a full run with
neither is refused. A dry run writes no file.

| File | Holds |
|---|---|
| `full_sync.json` | the report: every step's counts (under `steps` and at the top level), `timings_s`, `status` (`ok`, `refused` or `failed`) and `error`; written even when the run fails or is refused |
| `census.json` | one entry per attribute key: sample type, title, value type, role, `declared`, samples with a value, longest value, cast failures |
| `child_of_archive.tsv` | every CHILD_OF pair (child uuid, parent uuid, `declared`) before CHILD_OF is deleted; absent when the graph had none |
| `derived_from_undeclared_archive.tsv` | every undeclared DERIVED_FROM between two Sample nodes before it is deleted: child id, parent id, child uuid, parent uuid, and the edge's properties as JSON (uuids with backslash, tab, CR and LF escaped); absent when there was none |

`--verify --run-dir PATH` saves `gate_g.json` there, and `--catalog --run-dir PATH` saves `catalog_sync.json`.

### Lineage

MySQL's parent tokens are the truth for DERIVED_FROM between Sample nodes, so the lineage step makes the graph equal
to them. First every declared pair the graph lacks is created; a declared edge that already exists keeps its
properties. Then every DERIVED_FROM between two Sample nodes that MySQL does not declare (a pair a later Parent edit
left stale, a self-loop, a token that no longer resolves to that sample) is written to
`derived_from_undeclared_archive.tsv` and deleted in batches; the file is in place before the first delete. An edge
touching an `OrphanSample` is left alone. The report carries the step as `lineage_undeclared`
(`derived_from_between_samples`, `derived_from_undeclared`, `derived_from_deleted`). Gate G check 1 fails on any
undeclared edge still between two Sample nodes.

The modules, one concern each; `git ls-files nextseek_api/graph_sync` lists which have landed:

| Module | Holds |
|---|---|
| `projection.py` | pure: one sample row to its property map, type label and `search_text`; value casts |
| `catalog.py` | pure: the SampleType and Attribute catalog from SEEK and the dmac context tables |
| `sources.py` | MySQL readers: keyset-paged samples, project links, memberships, declared lineage |
| `cypher.py`, `writer.py` | the Neo4j statements and the chunked writer |
| `verify.py`, `run.py` | gate G and the ordered full and catalog runs |

## Running and testing

The pure modules and the command are unit-tested in the Django unit lane (`ci/README.md` "Running and testing"),
test files `nextseek_api/tests/test_graph_sync_*.py`. A run against real data happens only in the throwaway lane,
never against the live stack: `scripts/graph_search/lane.sh app graph_sync ...` (see
[`scripts/graph_search/README.md`](../../scripts/graph_search/README.md)).

## Depends on / depended on by

- Depends on Django's `seek` and `default` database connections, the `neo4j` driver and
  `settings.NEO4J_DATABASE`.
- Depended on by the lane scripts in `scripts/graph_search/` and, through the graph it writes, by
  [`nextseek_api/graph_search/`](../graph_search/README.md).
