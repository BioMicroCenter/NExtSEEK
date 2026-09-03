# Archived documentation

Every file under `docs/archive/` is kept for provenance only. Nothing here
describes current behaviour, and nothing here is maintained. Read the successor
named in the last column instead; where the column says "nothing, the work
shipped", the code itself is the record.

Nothing was deleted. Every file arrived here by `git mv`, so `git log --follow`
still reaches its full history.

Layout:

- `docs/archive/` (this directory): superseded documents that carried no date in
  their filename.
- `docs/archive/2026-07/` and `docs/archive/2026-08/`: the dated design and plan
  pairs, filed by the date in the filename.

## Undated documents

| File | What it covered | Why it is historical | Superseded by |
|---|---|---|---|
| `nessie-adhoc-question-inventory.md` | Snapshot of the real ad-hoc questions asked of Nessie, weighted by traffic. | Self-labelled a historical snapshot, and its recovery recipe no longer runs: `merged()` has rejected an overlay path since the 2026-08-04 corpus unification. | `docs/nessie-question-set-2026-08-06.md` carries its traffic-weighting conclusion forward; the corpus it deduped against is now `nessie_tests/corpus.json`. |
| `nessie-bayesian-mode-design.md` | Design for `--bayesian`, the paired mode that runs corpus variants through both engines and grades each answer twice. | Says of itself "design approved, plan not yet written". All three plans were written and executed. | Nothing, the work shipped: `bayesian.py`, `preflight.py`, `bayes_manifest.py`, `collect.py`, `export.py` and `output-skill-bayesian`, documented by the `nessie_tests/README.md` + `nessie_tests/CLAUDE.md` pair. |
| `nessie-bayesian-plan-1-unified-corpus.md` | Implementation plan collapsing the three-file corpus into one hand-owned `corpus.json`. | Executed. Its deliverable is the sole committed corpus; `overlay.json` and `retired.json` are gone. Its 283-variant / 314-turn baseline is three corpus generations stale against the 424 measured today. | `nessie_tests/corpus.json`, plus the `nessie_tests/README.md` + `nessie_tests/CLAUDE.md` pair. |
| `nessie-bayesian-plan-2-runner.md` | Implementation plan for the paired runner: forced routing, preflight, manifest, CLI flags. | Executed. Every file it specifies exists (`http_driver` force_route, `run_case`, `preflight.py`, `bayes_manifest.py`, `bayesian.py`). Its 130-variant selection is superseded by the committed 149. | Nothing, the work shipped; described by `nessie_tests/README.md` + `nessie_tests/CLAUDE.md`. |
| `nessie-bayesian-plan-3-evaluation.md` | Implementation plan for collection, export, double grading and the HiBayes rows. | Executed. `collect.py`, `export.py`, `merge_grades.py` and `output-skill-bayesian` all ship. Its 127-variant / 158-turn figures are superseded by the committed 149. | Nothing, the work shipped; described by `nessie_tests/README.md` + `nessie_tests/CLAUDE.md` and `nessie_tests/output-skill-bayesian/SKILL.md`. |
| `nessie-cc-rerun-2026-08-06.md` | A staged repay of the CC halves of the 127-variant 2026-08-06 study. | Never run. Its run directories (`nessie_bayes_full/`, `nessie_bayes_full_cc/`) are not in the repo, and the contamination it was written to work around was fixed by `eca15f6`. | `docs/nessie-question-set-2026-08-06.md`, which replaces the study with 149 re-ground-truthed questions and adds `memory.fresh_session_has_no_history` as the direct regression test. |
| `nessie-cc-task-families-skeleton.md` | Authoring skeleton for CC task families, proposing eight family names. | Filled: its first pass shipped as a probe file. None of its eight proposed family names exist in the current taxonomy. | `nessie_tests/probes/probe-cc-2026-07-31.json` for the pass itself; the 28-family remap in `nessie_tests/FAMILIES.json` / `corpus.json` and `docs/nessie-blocked-capabilities.md` for the taxonomy. |
| `nessie-corpus-additive-2026-08-06.md` | Record of the additive pass that took the selection from 127 to 152, and how to run its delta. | The additive half was taken but the selection moved on. | `docs/nessie-question-set-2026-08-06.md`, whose 149 is the committed selection and which resolves its named open items (the PRIDE bad question, the `tree.cel_descendants` grade). |
| `nessie-corpus-question-inventory.md` | Snapshot of 381 variants / 447 turns generated from `catalog.json` + `overlay.json`. | Self-labelled a historical snapshot. Both of its generating inputs have since been deleted as corpus inputs. | `nessie_tests/corpus.json` and the generated `docs/nessie-question-set-2026-08-06.md`. |
| `nessie-corpus-review-findings-2026-07-30.md` | Findings from the 548-question corpus review. | Already acted on: the review was executed on 2026-08-03, retiring 101 variants and correcting `route_capabilities.json`. | `docs/archive/2026-08/2026-08-03-nessie-hardening-handoff-1-harness-corpus.md`, which cites it as its input, and the retirements now carried in `nessie_tests/corpus.json`. |
| `nessie-corpus-rework-2026-08-06.md` | A proposed 122-variant corpus rework put to the operator. | Not taken. Only the additive half was accepted. | `docs/nessie-question-set-2026-08-06.md`, whose 149 is the committed selection. |
| `shared-memory-router-fix.md` | Commit note for `96b1d4a` on the retired `dev-260718` branch, shipped and validated live 2026-07-22. | Its central new file `nextseek_api/cc_assistant/cc_history.py` no longer exists, so its "Files changed" list would mislead a reader. | The `nextseek_api/cc_assistant/README.md` + `CLAUDE.md` pair, which documents `decide()` and its history feed at `README.md:27-34`. |
| `wave0-baseline.md` | Pre-merge test baseline and wave gate for branch `dev-260718`. | Its waves completed 2026-07-22; the gate has no later wave to gate. | `ci/pytest-baseline.txt` as the known-failure baseline (`ci/README.md:43`), and per lane each boundary's own README/CLAUDE pair. Its unfixed multi-instance items 4-5 survive at `startup/CLAUDE.md:97` and `startup/README.md:131`. |

## 2026-07

| File | What it covered | Why it is historical | Superseded by |
|---|---|---|---|
| `2026-07/2026-07-23-cross-mode-memory-reconciliation-design.md` | Design for reconciling the issue-#9 memory branch with the `dev` memory system: one shared store, symmetric NS and CC injection. | Executed and merged to `dev-v3-merge`. The reconciliation it argues for is the arrangement now in the tree. | Nothing, the work shipped; the `nextseek_api/cc_assistant/README.md` + `CLAUDE.md` pair documents the resulting `decide()` and history feed. |
| `2026-07/2026-07-23-cross-mode-memory-reconciliation-plan.md` | Task-by-task plan for that merge and the CC-turn projection. | Executed. Its conflict-resolution policy and merge instructions describe a branch state that no longer exists. | Nothing, the work shipped. |
| `2026-07/2026-07-23-luria-fetchngs-file-resolution-design.md` | Design for a Luria-only launch backend: local `/net/bmc-*` or SRR resolution, an on-cluster `fetchngs` pre-stage, local reference bias. | Executed as issue #2, merged, then validated green end to end on Luria. | Nothing, the work shipped: `chat_nextseek/src/chat_nextseek/luria/` including `fetchngs_helpers.py`, under the `chat_nextseek/README.md` + `CLAUDE.md` pair. |
| `2026-07/2026-07-23-luria-fetchngs-file-resolution-plan.md` | Task-by-task plan for the same work. | Executed. | Nothing, the work shipped. |
| `2026-07/2026-07-24-nessie-tests-router-harness-design.md` | Design for a top-level harness that drives cases through the real router, unifying three test silos (issues #31, #32, #33). | Built. The harness exists and has been run live many times since. | The `nessie_tests/README.md` + `nessie_tests/CLAUDE.md` pair. |
| `2026-07/2026-07-24-nessie-tests-router-harness-plan.md` | Task-by-task plan for the harness, built around a vendored `overlay.json` and 366 imported NS variants. | Executed, and its corpus architecture has since been replaced: `overlay.json` is gone and the single committed corpus is `nessie_tests/corpus.json`. | `nessie_tests/README.md` + `nessie_tests/CLAUDE.md`, and `nessie_tests/corpus.json` for the corpus shape. |

## 2026-08

| File | What it covered | Why it is historical | Superseded by |
|---|---|---|---|
| `2026-08/2026-08-03-nessie-hardening-design.md` | The hardening design: routing continuity, provider resilience, harness truthfulness, and the write boundary. | Executed on 2026-08-03. Several of its claims were found false during execution and are corrected by the companion corrections file. | `2026-08/2026-08-03-nessie-hardening-corrections.md` for its false claims; the `nessie_tests/README.md` + `nessie_tests/CLAUDE.md` pair for current behaviour. |
| `2026-08/2026-08-03-nessie-hardening-corrections.md` | Claims in the hardening design, plans and handoffs that turned out false at HEAD, verified during execution. | The correction record for a set of documents that are themselves now archived. It remains the most reliable of the eight, which is why it is filed with them. | Nothing; it is the corrective layer over the other seven 2026-08-03 files. |
| `2026-08/2026-08-03-nessie-hardening-handoff-1-harness-corpus.md` | Context handoff for the harness and corpus lane: environment, traps, cross-plan constraints, verified against `9b7954a`. | Every number was pinned to the tree at `9b7954a` on 2026-08-03 and the lane is complete. | `nessie_tests/README.md` + `nessie_tests/CLAUDE.md`. |
| `2026-08/2026-08-03-nessie-hardening-handoff-2-resilience-routing.md` | Context handoff for the provider-resilience and routing-continuity lane. | Same: pinned to `9b7954a`, lane complete. | `nessie_tests/README.md` + `nessie_tests/CLAUDE.md`, and `dmac_assistant/README.md` for the router. |
| `2026-08/2026-08-03-nessie-hardening-handoff-3-write-identity.md` | Context handoff for the write and identity boundary lane. | Same: pinned to `9b7954a`, lane complete. Its sample counts are stale. | `nessie_tests/README.md` + `nessie_tests/CLAUDE.md`; `docs/endpoint-authorization-register.md` for the authorization ruling. |
| `2026-08/2026-08-03-nessie-hardening-plan-1-harness-corpus.md` | Task-by-task plan for the harness and corpus lane. | Executed. | Nothing, the work shipped. |
| `2026-08/2026-08-03-nessie-hardening-plan-2-resilience-routing.md` | Task-by-task plan for the resilience and routing lane. | Executed. | Nothing, the work shipped. |
| `2026-08/2026-08-03-nessie-hardening-plan-3-write-identity.md` | Task-by-task plan for the write and identity lane. | Executed. | Nothing, the work shipped. |
| `2026-08/2026-08-06-unified-sample-download-readme-design.md` | Design routing every sample-download control through one API and opening every workbook on a README sheet. | Shipped. The single endpoint, `sample_workbook.py` and the README sheet all exist. | `docs/sample-download-workflow.md`, which describes the download surface as it now is, and the `nextseek_api/services/README.md` + `CLAUDE.md` pair. |
| `2026-08/2026-08-06-unified-sample-download-readme-plan.md` | Task-by-task plan for the same work. | Executed. | `docs/sample-download-workflow.md`. |
| `2026-08/2026-08-19-download-readme-column-definitions-design.md` | Design indexing every workbook column on the README sheet with a plain-English meaning. | Shipped. It also carries its own rename warning: `sample_fields_context` became `sample_attributes_unique` two days after it was written. | `docs/sample-download-workflow.md`; `seek/models/nextseek.py` for the renamed model. |
| `2026-08/2026-08-19-download-readme-column-definitions-plan.md` | Task-by-task plan for the same work, under the same stale table name. | Executed. | `docs/sample-download-workflow.md`. |
| `2026-08/2026-08-21-download-provenance-order-and-house-vocabularies-design.md` | Design ordering the workbook by how samples were generated, widening dropdowns, and offering house vocabularies instead of GEO's. | Shipped, and the flow sheet it created was itself replaced four days later by the tree. | `docs/sample-download-workflow.md`; `2026-08/2026-08-25-provenance-tree-sheet-design.md` for the sheet that replaced its own. |
| `2026-08/2026-08-21-download-provenance-order-and-house-vocabularies-plan.md` | Task-by-task plan, including the extraction of `sample_provenance.py` out of `sample_workbook.py`. | Executed: `nextseek_api/services/sample_provenance.py` exists. | `docs/sample-download-workflow.md`. |
| `2026-08/2026-08-21-publication-links-design.md` | Design for showing which published paper a sample appears in (DOI / PMID on the study, inherited by membership) and the reverse lookup. | Shipped, and applied to production on 2026-08-26. | `sample_publication_attributes/archive/PROD_ROLLOUT.md` for the migration record and reverse recipe; `seek/doi_extract.py` and `nextseek_api/management/commands/backfill_publication_attributes.py` for the code; the Publication section of `chat_nextseek/src/chat_nextseek/context/capabilities.md` for the shipped capability. |
| `2026-08/2026-08-21-publication-links-plan.md` | Task-by-task plan for the same work. | Executed and rolled out. | Same as its design, above. |
| `2026-08/2026-08-25-provenance-tree-sheet-design.md` | Design replacing the flat "How this data flowed" chains with an indented tree. | Shipped. | Nothing, the work shipped: `build_provenance_tree` at `nextseek_api/services/sample_provenance.py:103`. Described by `docs/sample-download-workflow.md`. |
| `2026-08/2026-08-25-provenance-tree-sheet-plan.md` | Task-by-task plan for the same work. | Executed. | Nothing, the work shipped. |
