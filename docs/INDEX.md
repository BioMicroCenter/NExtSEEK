# docs/

Cross-cutting documentation only: things that belong to no single folder.
A folder's own docs live beside its code (`README.md`, plus `CLAUDE.md` when it has rules).
A doc that tracked code cites must itself be tracked, or the citation points at a folder README.

## Current

| File | Kind | Read when | Tracking issue |
|---|---|---|---|
| [`ISSUE-CONVENTIONS.md`](ISSUE-CONVENTIONS.md) | convention | filing any GitHub issue; `scripts/validate_issue.py` enforces it | |
| [`endpoint-authorization-register.md`](endpoint-authorization-register.md) | register | changing who may call an endpoint. Incomplete for routes added after 2026-08-11; `ci/routes.py` is the full route list. Its `is_staff` question was ruled by #74 and #75 (admin means `is_superuser`); its per-endpoint buckets are still open under #64 | #64 |
| [`neo4j-programmatic-access.md`](neo4j-programmatic-access.md) | runbook | querying Neo4j over HTTP, Browser or bolt, or rotating its password | |
| [`neo4j-schema.md`](neo4j-schema.md) | reference | reading or writing the sample graph: what v1.0 holds and what graph_search's v1.1 builds | |
| [`sample-download-workflow.md`](sample-download-workflow.md) | explanation | changing any "Download samples" control or the workbook | |
| [`UI.md`](UI.md) | snapshot | finding a page's route, view and template. Dated 2026-09-03; check it against the tree | |
| [`superpowers/specs/2026-09-01-nextseek-ci-comprehensive-coverage-design.md`](superpowers/specs/2026-09-01-nextseek-ci-comprehensive-coverage-design.md) | live spec | extending CI coverage past tier T0 | #104 |
| [`superpowers/specs/2026-09-11-nessie-ci-lane-design.md`](superpowers/specs/2026-09-11-nessie-ci-lane-design.md) | live spec | changing what the Nessie CI lane (`ci/smoke/test_nessie.py`) proves, its switches, or its budget; `ci/smoke/README.md` "Nessie lane" is the operator's view | |
| [`superpowers/plans/2026-09-11-nessie-ci-lane.md`](superpowers/plans/2026-09-11-nessie-ci-lane.md) | plan | reading how the Nessie lane was built, task by task, and what changed on the way | |
| [`superpowers/specs/2026-09-14-graph-search-poc-design.md`](superpowers/specs/2026-09-14-graph-search-poc-design.md) | draft spec | changing the graph_search proof of concept: metadata in the graph, the catalog, membership scope, the endpoint and its benchmark | |
| [`superpowers/plans/2026-09-14-graph-search-poc.md`](superpowers/plans/2026-09-14-graph-search-poc.md) | plan | executing or reviewing the graph_search proof of concept, task by task | |
| [`superpowers/specs/2026-09-15-graph-search-nessie-design.md`](superpowers/specs/2026-09-15-graph-search-nessie-design.md) | draft spec | changing how Nessie reads the metadata graph (the live catalog reader, the rendered context, the per-label guard), or the evidence POC that compares its graph agent with its API agent; stage A1 (server-injected scope) is designed here | |
| [`superpowers/plans/2026-09-15-graph-search-nessie.md`](superpowers/plans/2026-09-15-graph-search-nessie.md) | plan | executing or reviewing graph_search follow-up 1: the build, the venue, ground truth, the paid runs and the A1 stage, task by task | |
| [`superpowers/specs/2026-09-15-graph-search-sync-design.md`](superpowers/specs/2026-09-15-graph-search-sync-design.md) | draft spec | changing how graph 2.0 stays in sync with every writer: the hooks, the nightly targeted sync, the weekly full sync, batch upload's graph stage, the deletion rule, the loop and its CI gates | |
| [`superpowers/specs/2026-09-15-graph-search-sync-inventory.md`](superpowers/specs/2026-09-15-graph-search-sync-inventory.md) | inventory | finding who writes a table the graph reads: every writer, route and code-scan site, cross-checked, with the recon's corrections | |
| [`superpowers/plans/2026-09-15-graph-search-sync.md`](superpowers/plans/2026-09-15-graph-search-sync.md) | plan | executing or reviewing the graph sync work as four Workflow runs, task by task | |
| [`superpowers/specs/2026-09-16-graph-behaviour-tests-design.md`](superpowers/specs/2026-09-16-graph-behaviour-tests-design.md) | draft spec | making a 1.2 full sync fit inside the live neo4j's transaction bound, and proving by running them that each write path changes the graph | |
| [`superpowers/plans/2026-09-16-graph-behaviour-tests.md`](superpowers/plans/2026-09-16-graph-behaviour-tests.md) | plan | executing the transaction-bound fix and the behavioural write lane, task by task | |

`docs/superpowers/` is gitignored by default; only files named by a negation in `.gitignore` are tracked.

## Elsewhere

- Nessie docs are listed in [`NessieAI/README.md`](../NessieAI/README.md).
- Superseded docs: [`archive/INDEX.md`](archive/INDEX.md) for everything outside Nessie,
  [`NessieAI/history/INDEX.md`](../NessieAI/history/INDEX.md) for Nessie.
