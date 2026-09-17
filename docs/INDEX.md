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
| [`sample-download-workflow.md`](sample-download-workflow.md) | explanation | changing any "Download samples" control or the workbook | |
| [`UI.md`](UI.md) | snapshot | finding a page's route, view and template. Dated 2026-09-03; check it against the tree | |
| [`nfcore-capability-expansion.md`](nfcore-capability-expansion.md) | explanation | answering "what analyses can Nessie run?" in plain English, for a non-engineer | |
| [`btc-gbm-sequencingtype-mislabelling.md`](btc-gbm-sequencingtype-mislabelling.md) | finding | reading how 54 gene-expression libraries came to be labelled "Single Cell TCR" in study 241114SHA. Fixed and verified 2026-08-17; two of the ad-hoc scripts it cites were never committed | |
| [`2026-08-07-pipeline-param-inference-design.md`](2026-08-07-pipeline-param-inference-design.md) | design | inferring species and library facts from NExtSEEK metadata to fill nf-core params. Approved, not yet built: the modules and audit script it cites do not exist in the tree | |
| [`2026-08-07-nfcore-launch-path-presentation-design.md`](2026-08-07-nfcore-launch-path-presentation-design.md) | design | the explainer page for how a chat message becomes a job on Luria. The page itself is a meeting artifact built outside this repo; this doc is the maintained record | |
| [`2026-08-07-nfcore-launch-path-presentation-plan.md`](2026-08-07-nfcore-launch-path-presentation-plan.md) | plan | reading how that explainer page was to be built, task by task | |
| [`superpowers/specs/2026-09-01-nextseek-ci-comprehensive-coverage-design.md`](superpowers/specs/2026-09-01-nextseek-ci-comprehensive-coverage-design.md) | live spec | extending CI coverage past tier T0 | #104 |
| [`superpowers/specs/2026-09-11-nessie-ci-lane-design.md`](superpowers/specs/2026-09-11-nessie-ci-lane-design.md) | live spec | changing what the Nessie CI lane (`ci/smoke/test_nessie.py`) proves, its switches, or its budget; `ci/smoke/README.md` "Nessie lane" is the operator's view | |
| [`superpowers/plans/2026-09-11-nessie-ci-lane.md`](superpowers/plans/2026-09-11-nessie-ci-lane.md) | plan | reading how the Nessie lane was built, task by task, and what changed on the way | |

`docs/superpowers/` is gitignored by default; only files named by a negation in `.gitignore` are tracked.

## Elsewhere

- Nessie docs are listed in [`NessieAI/README.md`](../NessieAI/README.md).
- Superseded docs: [`archive/INDEX.md`](archive/INDEX.md) for everything outside Nessie,
  [`NessieAI/history/INDEX.md`](../NessieAI/history/INDEX.md) for Nessie.
