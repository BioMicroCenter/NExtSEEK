# Plan 018 V4-0 ownership map (draft)

**Status:** accepted by maintainer (2026-08-11) — see `plan018-v4-0-ownership-map-acceptance.json`.  
**Base:** `6881b6a8` (`ultraplan/hibayes-eval-routing` @ `/home/taishajo/work/NExtSEEK-plan018`)  
**Eval vendor:** `dmac-assistant@dcca50c`  
**Nessie paired harness tip:** `origin/dev-v4-merge@3fe71670` (`nessie_tests` tree `35f5b706…`)  
**Recorded:** 2026-08-11

Canonical owner = the task that may create/modify the path. Consumers may read but not redefine contracts.

## Port sources (read-only until owning task copies)

| Surface | Source identity | Destination owner | Notes |
|---|---|---|---|
| HiBayes judge + models | `dmac-assistant@dcca50c` `tools/e2e/functional_evaluator{,_models}.py` | Task 6 → `nextseek_api/eval/judge.py` (+ models) | Vendor verbatim except import paths |
| HiBayes fit packages | `dmac-assistant@dcca50c` `src/dmac_assistant/eval/hibayes_*` | Task 6 → under `nextseek_api/eval/` | Copy, do not rewrite control flow |
| HiBayes exporter / enums / expected_behavior / functional_inputs | `tools/hibayes/*` (except artifact_validator) | Task 6 | Per Task 6 inventory |
| Artifact validator | **fresh write** (V9-A) | Task 7b → `nextseek_api/eval/artifact_validity.py` | Do **not** port `tools/hibayes/artifact_validator.py` |
| Eval container | `dmac-assistant` `Dockerfile.hibayes-eval` | Task 6 → `docker/eval/Dockerfile` | After Phase 2, no dmac-assistant checkout dependency |
| Ordinary + paired Nessie producer | `origin/dev-v4-merge@3fe71670` `nessie_tests/**` | V4-2 | Port/review onto implementation base; do not copy from live `/app` |
| `set3_final` evidence | `~/work/NExtSEEK-dev/testquestions-2026-08-07` (V13-A hashes) | V4-1/V4-3/V4-4 consumers (read-only) | No replacement pa ired run |

## Product surfaces (to create/modify on implementation base)

| Owner | Path(s) | Produces | Consumed by |
|---|---|---|---|
| V4-0 / Task 1 | migrations + `CorpusVersion` / `CorpusPromotion` / stack provenance models | immutable corpus + promotion rows | Task 2/5, Task 9, V4-2 assembly |
| Task 0 (V5) | `nextseek_api/cc_assistant/tests/conftest.py` | shared fixtures (incremental) | all later test modules |
| Task 1 | `nextseek_api/assistant/models_db.py` (+ migration leaf after 0009) | `TurnLedger` (and related) | Task 2/5 writers, export |
| Task 2 / 5 | `nextseek_api/cc_assistant/turn_ledger.py` + route call sites | ledger writes | Task 7 export |
| Task 3 / V4-6 | `router.baml` (both trees), `router.py`, `family_labels.py` | dynamic classifier labels from corpus `families` | Task 12 selector, provenance |
| Task 6 | `nextseek_api/eval/**` vendor tree, `docker/eval/Dockerfile`, lockfile deps | vendored judge/fit | Tasks 7–10 |
| Task 7 | `nextseek_api/eval/export.py` | versioned EvalRow / arms | Task 8–10, V4-4 |
| Task 7b | `artifact_validity.py`, `artifact_sources.py` | artifact status axis | export + V9 pins |
| Task 8 | `judge_cache.py` | fingerprint cache | Task 9 |
| Task 9 | `eval/tasks.py` Celery nightly + reservation | paid-gated runs | Task 10 |
| Task 10 / V4-4 | fit adapter + `publish.py` | immutable posterior generations | Task 11/12 |
| Task 11 | `playbook.py` | playbook consumer | monitoring only |
| Task 12 / V4-6 | posterior selector (flag-gated) | `route_source="posterior"` | online routing |
| Task 13 / V5 | coverage manifest + release/recovery proofs | gate | Phase close |
| V4-7 | schema/type boundary online vs paired | hard separation | fitter must refuse online rows |
| V4-8 | authorization/reservation/spend controls | budget safety | Task 9 paid path |
| V4-9 | deployment/recovery docs+tests | non-destructive recovery only | ops |

## Explicit non-owners / voids

- Live container `/app` and SA clone: **evidence only**, never port source or harness mount for implementation.
- Historical File Structure `0010_turn_ledger.py` name: void; renumber from actual leaf.
- Editing `route_capabilities.json` for classifier labels: void (V6/V7).
- Porting `artifact_validator.py`: void (V9-A).
- Replacing/rerunning `set3_final` for DONE: void (V13-B).

## Open before product code (post-baseline)

1. ~~Maintainer accept/amend this ownership map.~~ **done**
2. ~~Isolated worktree-mounted harness recipe + baseline suite on `6881b6a8`.~~ **done** (§3.1 + §3.3)
3. ~~`evidence/plan018-controlling-contract.json` full task→clause map.~~ **done** (`v4-0-complete-for-plan-tasks`; see `plan018-controlling-contract-summary.md`)
4. ~~Port provenance file enum + newer-tip review.~~ **done** (`plan018-v4-0-port-provenance.*`)
5. ~~Forward migrate empty + prod-shaped disposable.~~ **done** (SA `startup/seed` → leaf `0009`; live DB untouched)
6. Maintainer closeout of V4-0 → authorize **V4-1** (mechanical; no product port).
