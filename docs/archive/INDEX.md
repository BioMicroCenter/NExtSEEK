# Archived documentation

Every file under `docs/archive/` is kept for provenance only. Nothing here describes current
behaviour, and nothing here is maintained. Read the successor named in the last column instead;
where it says "nothing, the work shipped", the code itself is the record.

Every file arrived here by `git mv`, so `git log --follow` still reaches its history.
Nessie history lives in `NessieAI/history/INDEX.md`, not here.

Layout: `docs/archive/<yyyy-mm>/`, filed by the date in the filename. Append rows; never rewrite old ones.

## 2026-08

| File | What it covered | Why it is historical | Superseded by |
|---|---|---|---|
| `2026-08/2026-08-06-unified-sample-download-readme-design.md` | Routing every sample-download control through one API and opening every workbook on a README sheet. | Shipped. | `docs/sample-download-workflow.md` and `nextseek_api/services/README.md`. |
| `2026-08/2026-08-06-unified-sample-download-readme-plan.md` | Task-by-task plan for the same work. | Executed. | `docs/sample-download-workflow.md`. |
| `2026-08/2026-08-19-download-readme-column-definitions-design.md` | Indexing every workbook column on the README sheet with a plain-English meaning. | Shipped. It carries its own rename warning: `sample_fields_context` became `sample_attributes_unique`. | `docs/sample-download-workflow.md`; `seek/models/nextseek.py` for the renamed model. |
| `2026-08/2026-08-19-download-readme-column-definitions-plan.md` | Task-by-task plan, under the same stale table name. | Executed. | `docs/sample-download-workflow.md`. |
| `2026-08/2026-08-21-download-provenance-order-and-house-vocabularies-design.md` | Ordering the workbook by how samples were generated, and offering house vocabularies. | Shipped; its flow sheet was replaced four days later by the tree. | `docs/sample-download-workflow.md`; `2026-08/2026-08-25-provenance-tree-sheet-design.md`. |
| `2026-08/2026-08-21-download-provenance-order-and-house-vocabularies-plan.md` | Task-by-task plan, including the extraction of `sample_provenance.py`. | Executed. | `docs/sample-download-workflow.md`. |
| `2026-08/2026-08-21-publication-links-design.md` | Showing which published paper a sample appears in (DOI / PMID), and the reverse lookup. Cited by `seek/doi_extract.py`; keep this path. | Shipped, and applied to production on 2026-08-26. | `2026-08/publication-rollout/sample_publication_attributes/archive/PROD_ROLLOUT.md`; `seek/doi_extract.py` and `nextseek_api/management/commands/backfill_publication_attributes.py`; the Publication section of `NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md`. |
| `2026-08/2026-08-21-publication-links-plan.md` | Task-by-task plan for the same work. | Executed and rolled out. | Same as its design, above. |
| `2026-08/2026-08-25-provenance-tree-sheet-design.md` | Replacing the flat "How this data flowed" chains with an indented tree. | Shipped. | Nothing, the work shipped: `build_provenance_tree` in `nextseek_api/services/sample_provenance.py`. |
| `2026-08/2026-08-25-provenance-tree-sheet-plan.md` | Task-by-task plan for the same work. | Executed. | Nothing, the work shipped. |
| `2026-08/publication-rollout/prod_publication_transfer/` | Transfer of publication records from fairdata-dev to production (2026-08-26), plus a generator for the rest. | Partially applied and off the critical path; no code reads it. | The publication-links design, above. |
| `2026-08/publication-rollout/sample_publication_attributes/` | DOI/PMID-as-sample-attribute SQL and the production rollout record with verify and reverse recipes. | Applied to production 2026-08-26. | Its own `archive/PROD_ROLLOUT.md` is the record. |

## 2026-09

| File | What it covered | Why it is historical | Superseded by |
|---|---|---|---|
| `2026-09/2026-09-01-nextseek-ci-cd-design.md` | The original CI/CD design: five decisions, four flows. | Superseded by the full spec, and CI is built. | `ci/README.md`, `ci/CLAUDE.md`. |
| `2026-09/2026-09-01-nextseek-ci-cd-full-spec.md` | The full CI implementation spec. | Built. | `ci/README.md`, `ci/CLAUDE.md`. |
| `2026-09/2026-09-01-ci-increment-1-skeleton-and-safety.md` | Plan for CI increment 1: route registry, GuardedSession, T0, the `startup ci` shim. | Executed and merged. | `ci/README.md`. |
| `2026-09/2026-09-01-sample-attributes-gui-rewrite-design.md` | A frontend-only rewrite of `/seek/samples/attributes/`. | Built the same day. | `seek/templates/sampleAttributes.html`; `nextseek_api/attributes/README.md`. |
| `2026-09/2026-09-02-sample-type-requirements-sdd-ledger.md` | Task-by-task execution ledger for the sample-type-requirements plan (was `.superpowers/sdd/progress.md`). | Complete and merged. Its plan and spec were never committed. | Nothing, the work shipped. |
| `2026-09/2026-09-03-ui-snapshot.md` | A page-by-page map of UI routes, views and templates, dated 2026-09-03 (was `docs/UI.md`). | Stale after the navbar, home and catalog redesign merged; replaced by a maintained guide. | `docs/ui/README.md`. |
| `2026-09/session-debug-endpoint-design.md` | Design for the admin session-inspection endpoint `/nextseek_api/nessie/`. | Built in the same commit as the design. | `nextseek_api/services/nessie.py`, `nextseek_api/assistant/session_debug.py`, `nextseek_api/assistant/README.md`. |
