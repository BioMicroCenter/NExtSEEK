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
```

- `--full` runs the whole ordered sync (design section 6, "Order of a full run").
- `--catalog` writes the catalog nodes only.
- `--verify` is read-only and runs gate G; it exits 1 when a check fails.
- The command refuses to run against the live stack's Neo4j (service name `neo4j`) unless
  `--i-mean-the-live-graph` is passed, and exits 2 on a refusal.

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
