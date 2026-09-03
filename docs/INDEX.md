# docs/

Cross-cutting documentation: the things that belong to no single code boundary.
Documentation that belongs to one boundary lives beside that code, as the
`README.md` + `CLAUDE.md` + `CITATIONS.txt` triple.

## Current

Every `.md` at the top level of `docs/`. All seven were re-verified against the
tree on 2026-09-03.

| File | What it is |
|---|---|
| `ISSUE-CONVENTIONS.md` | The live convention for filing issues on a public repo, drift-guarded in code by `nextseek_api/cc_assistant/tests/test_issue_conventions_guard.py` and cited as the rule by the root `CLAUDE.md`, `AGENTS.md` and the `scripts/` pair. |
| `dev-v5-merge-decisions.md` | The standing ruling on `route_capabilities.json` from the dev-v5 merge: do not pin the pre-merge file, do not teach the generator to honour `route_policy`. Pinned by `nextseek_api/assistant/tests/test_route_capabilities.py:569`; its gate is still open. |
| `endpoint-authorization-register.md` | The authorization register for every endpoint, cited as the ruling by `nextseek_api/CLAUDE.md:153`, `nextseek_api/urls.py:61` and `nextseek_api/tests/test_api_docs_authentication.py`. Its headline question, whether `is_staff` still means admin, is explicitly unruled. |
| `neo4j-programmatic-access.md` | Runbook for the three Neo4j access paths, the optional nginx drop-in that multiplexes bolt onto 443, and password rotation. Cited by `docker/CLAUDE.md:121`. |
| `nessie-blocked-capabilities.md` | The 28-family capability table with 255 per-capability blockers at `file:line`, the five fields already in the payload and the one still missing. Counts are pinned to 2026-08-04; no successor document carries any of it. |
| `nessie-question-set-2026-08-06.md` | The ground-truth reference for the corpus as committed: the 149-variant bayesian selection, generated from `nessie_tests/corpus.json` by `nessie_tests/scripts/build_doc.py`. Section 10 carries six unruled operator items. |
| `sample-download-workflow.md` | How sample download works now, end to end. Cited as the live explanation by `nextseek_api/services/CLAUDE.md:210`, the module docstring at `nextseek_api/services/sample_workbook.py:5`, `nextseek_api/tests/test_download_call_sites.py` and `startup/seed/README.md:21`. |

Two subdirectories hold current material of their own: `docs/superpowers/`
(design specs and implementation plans, mostly gitignored) and
`docs/testing-review/` (the three 2026-07 testing reviews that the harness
design was built from).

## Historical

Superseded documentation lives under `docs/archive/`, filed as
`docs/archive/` for undated documents and `docs/archive/2026-07/` and
`docs/archive/2026-08/` for the dated design and plan pairs.

Read `docs/archive/INDEX.md` first. It carries one row per archived file saying
what it covered, why it is historical, and what supersedes it. Nothing under
`docs/archive/` describes current behaviour, and nothing there is maintained.

Nothing was deleted. Every archived file was moved with `git mv`, so
`git log --follow` still reaches its full history.
