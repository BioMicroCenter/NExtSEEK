# NessieAI/history/

Frozen records. Nothing here describes current behaviour and nothing here is maintained:
files keep the paths they were written with. Pytest never collects this tree and the image excludes it.
Append a row when something new arrives; never rewrite an old one.

## Folders

| Folder | What it holds | Read instead, for current behaviour |
|---|---|---|
| `cc/` | Container-CC Steps 1b to 7: superseded specs and plans, review logs, live evidence, the Step 7 live E2E record. Start at `NessieAI/history/cc/archive/INDEX.md` | `NessieAI/cc/README.md`, `NessieAI/cc/DEPLOY.md` |
| `ns/` | paid real-stack acceptance records for the native granular ops (2026-06-12, 2026-06-25) | `NessieAI/ns/README.md` |
| `plan018/` | Plan 018 (HiBayes) closeouts and evidence, verifier scripts, SDD task reports (`sdd/`), the Task 8 rehearsal compose file, the Task 6 image, the test-question manifest builder, and the plan itself (`2026-07-31-hibayes-eval-routing.md`) | `NessieAI/hibayes/README.md` |
| `plan005/` | the Plan 005 closeout protocol, sign-offs and schemas, pinned to digests | `NessieAI/build_tools/README.md` |
| `retired/` | the chat_nextseek agent atlas page (`agent_atlas.html`), the chat_nextseek snapshot-sync script, the old e2e issue note, retired chat_frontend screenshots and HTML reports, `post_uv_sync.sh` and its GEO templates | the live unit docs |
| `docs/` | superseded Nessie design docs, plans, handoffs and reviews (table below) | the successor in each row |

Evidence files may hold data about human subjects. Never quote them in a doc, issue or commit.

Two records landed here rather than in `NessieAI/docs/`, where the NessieAI move's audit first placed
them: the 2026-07-24 testing reviews (`docs/testing-review/`, the design input of a harness that has
since shipped) and the HiBayes plan (in `plan018/`, beside the rest of that plan's records). The
HiBayes design spec stays live in `NessieAI/docs/`.

## docs/

### Undated

| File | What it covered | Superseded by |
|---|---|---|
| `docs/nessie-adhoc-question-inventory.md` | snapshot of real ad-hoc questions asked of Nessie | `NessieAI/docs/nessie-question-set-2026-08-06.md`; the corpus `NessieAI/tests/nessie_tests/corpus.json` |
| `docs/nessie-bayesian-mode-design.md` | design for `--bayesian`, the paired dual-engine mode | shipped; `NessieAI/tests/nessie_tests/README.md` |
| `docs/nessie-bayesian-plan-1-unified-corpus.md` | plan collapsing the three-file corpus into `corpus.json` | shipped; `NessieAI/tests/nessie_tests/corpus.json` |
| `docs/nessie-bayesian-plan-2-runner.md` | plan for the paired runner | shipped; `NessieAI/tests/nessie_tests/README.md` |
| `docs/nessie-bayesian-plan-3-evaluation.md` | plan for collection, export, double grading | shipped; `NessieAI/tests/nessie_tests/output-skill-bayesian/SKILL.md` |
| `docs/nessie-cc-rerun-2026-08-06.md` | a staged rerun of the CC halves of a 2026-08-06 study, never run | `NessieAI/docs/nessie-question-set-2026-08-06.md` |
| `docs/nessie-cc-task-families-skeleton.md` | authoring skeleton for CC task families | `NessieAI/tests/nessie_tests/FAMILIES.json`, `NessieAI/docs/nessie-blocked-capabilities.md` |
| `docs/nessie-corpus-additive-2026-08-06.md` | the additive pass from 127 to 152 variants | `NessieAI/docs/nessie-question-set-2026-08-06.md` |
| `docs/nessie-corpus-question-inventory.md` | snapshot of a retired corpus shape | `NessieAI/tests/nessie_tests/corpus.json` |
| `docs/nessie-corpus-review-findings-2026-07-30.md` | findings of the 548-question corpus review | `docs/2026-08/2026-08-03-nessie-hardening-handoff-1-harness-corpus.md` (this folder) |
| `docs/nessie-corpus-rework-2026-08-06.md` | a proposed corpus rework, not taken | `NessieAI/docs/nessie-question-set-2026-08-06.md` |
| `docs/shared-memory-router-fix.md` | commit note for a retired branch's router fix | `NessieAI/router/README.md` |
| `docs/wave0-baseline.md` | pre-merge test baseline for a retired branch | `ci/pytest-baseline.txt` |

### 2026-07

| File | What it covered | Superseded by |
|---|---|---|
| `docs/2026-07/2026-07-23-cross-mode-memory-reconciliation-design.md` | one shared memory store, symmetric NS and CC injection | shipped; `NessieAI/router/README.md`, `NessieAI/cc/README.md` |
| `docs/2026-07/2026-07-23-cross-mode-memory-reconciliation-plan.md` | plan for the same | shipped |
| `docs/2026-07/2026-07-23-luria-fetchngs-file-resolution-design.md` | Luria-only launch backend with an on-cluster fetchngs pre-stage | shipped; `NessieAI/chat_nextseek/README.md` |
| `docs/2026-07/2026-07-23-luria-fetchngs-file-resolution-plan.md` | plan for the same | shipped |
| `docs/2026-07/2026-07-24-nessie-tests-router-harness-design.md` | a harness that drives cases through the real router | shipped; `NessieAI/tests/nessie_tests/README.md` |
| `docs/2026-07/2026-07-24-nessie-tests-router-harness-plan.md` | plan for the same | shipped; `NessieAI/tests/nessie_tests/corpus.json` for the corpus shape |

### 2026-08

| File | What it covered | Superseded by |
|---|---|---|
| `docs/2026-08/2026-08-03-nessie-hardening-design.md` | routing continuity, provider resilience, harness truthfulness, write boundary | `docs/2026-08/2026-08-03-nessie-hardening-corrections.md` for its false claims; `NessieAI/tests/nessie_tests/README.md` |
| `docs/2026-08/2026-08-03-nessie-hardening-corrections.md` | claims in the other seven files that proved false | nothing; it is the corrective layer |
| `docs/2026-08/2026-08-03-nessie-hardening-handoff-1-harness-corpus.md` | handoff for the harness and corpus lane | `NessieAI/tests/nessie_tests/README.md` |
| `docs/2026-08/2026-08-03-nessie-hardening-handoff-2-resilience-routing.md` | handoff for resilience and routing | `NessieAI/tests/nessie_tests/README.md`, `NessieAI/dmac_assistant/README.md` |
| `docs/2026-08/2026-08-03-nessie-hardening-handoff-3-write-identity.md` | handoff for the write and identity boundary | `NessieAI/tests/nessie_tests/README.md`, `docs/endpoint-authorization-register.md` |
| `docs/2026-08/2026-08-03-nessie-hardening-plan-1-harness-corpus.md` | plan for the harness and corpus lane | shipped |
| `docs/2026-08/2026-08-03-nessie-hardening-plan-2-resilience-routing.md` | plan for resilience and routing | shipped |
| `docs/2026-08/2026-08-03-nessie-hardening-plan-3-write-identity.md` | plan for the write and identity lane | shipped |

### Reviews

| File | What it covered | Superseded by |
|---|---|---|
| `docs/testing-review/` (3 files) | the 2026-07-24 testing reviews the router-aware harness was designed from | `NessieAI/tests/README.md`, `NessieAI/tests/nessie_tests/README.md` |
