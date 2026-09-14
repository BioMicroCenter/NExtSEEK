# `nextseek_api/graph_search/`

## What this is

The engine behind `POST /nextseek_api/samples/graph_search/`: sample search answered from the Neo4j graph (schema
v1.1, [`docs/neo4j-schema.md`](../../docs/neo4j-schema.md)) instead of `advanced_search`'s full scan of MySQL. It
accepts `advanced_search`'s request body plus an optional `extensions` field (paired attribute filters, ranges and a
lineage condition), applies the same visibility rules, pages inside the database, and hydrates only the returned page
from MySQL. The contract, the matching rules and the declared differences from `advanced_search` are in the
[design](../../docs/superpowers/specs/2026-09-14-graph-search-poc-design.md), section 7; the build order is the
[plan](../../docs/superpowers/plans/2026-09-14-graph-search-poc.md).

The HTTP layer is a thin ViewSet, `GraphSearchViewSet`, in the `graph_search` module of
[`nextseek_api/services/`](../services/README.md); this package holds everything below it.

## Surface

The modules, one concern each; `git ls-files nextseek_api/graph_search` lists which have landed:

| Module | Holds |
|---|---|
| `scope.py` | `resolve_scope(user)`: superuser, or the caller's project ids read from MySQL membership |
| `query.py`, `lucene.py` | the pure query builder: validated request plus scope to one page statement and one count statement; fulltext escaping |
| `catalog_cache.py`, `hydrate.py` | the catalog read from the graph and cached; the page's rows read from MySQL by primary key |
| `service.py` | `search(...)` for the ViewSet and `all_ids(...)` for the parity harness |

Rules every module keeps:

- Scope is added by the query builder from the server-side `Scope`, never from the request or from generated Cypher.
- Every value is a query parameter; property names come from the catalog and are backtick-quoted.
- Queries run in `session.execute_read` with a timeout, and page with `ORDER BY s.id SKIP $skip LIMIT $limit`.

## Running and testing

Unit tests run in the Django unit lane (`ci/README.md` "Running and testing"), test files
`nextseek_api/tests/test_graph_search_*.py`. Parity against `advanced_search` runs in the throwaway lane
([`scripts/graph_search/README.md`](../../scripts/graph_search/README.md)).

## Depends on / depended on by

- Depends on the graph written by [`nextseek_api/graph_sync/`](../graph_sync/README.md), Django's `seek` connection
  for scope and hydration, the `neo4j` driver, and the request and response models in `nextseek_api/models.py`.
- Depended on by the ViewSet and by the lane's parity and benchmark scripts.
