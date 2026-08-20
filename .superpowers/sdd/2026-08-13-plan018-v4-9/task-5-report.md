# Plan 018 V4-9 Task 5 report

## Outcome

**Status: PASS as of 2026-08-18.** The source-bound critical mutation/fault
manifest enumerates **73 changed faults** across all thirteen plan-required
categories: routing, exclusions, conservation, DD-44 aggregation, pair
dependence, winner selection, hashes, activation, fallback, spend reservation,
evidence provenance, migration/version guards, and recovery ordering.

The pinned deployed-image lane selected and executed **65 exact fast tests**:
65 passed, 0 failed, 0 errors, 0 skipped, 0 xfailed, and 0 deselected in 3.19
seconds. The established disposable MySQL 8.0 Lane M executed **22 tests**,
including all eight exact real-store kill nodes: 22 passed with no
nonexecution in 77.76 seconds under `REPEATABLE-READ`. The complete gate took
**151.493 seconds**, below its hardware-aware 900-second wall cap.

Every enumerated mutant is mapped to at least one collected kill node and is
recorded `KILLED`. The manifest refuses unchanged definitions and a global
mutation-score substitute. The DD-44 entries are derived from the prior
fourteen-mutant manifest; the remaining faults are explicit, finite
fault-injection, boundary, structural, or real-store oracles. This gate does not
claim that an unconstrained mutation engine modified every source expression.

Machine-checkable evidence:

- `evidence/plan018-v4-9-task5-mutation-manifest.json` — 73 exact fault
  definitions, collected kill nodes, and source/test/control SHA-256 bindings;
- `evidence/plan018-v4-9-task5-collection.txt` — 65 exact fast node IDs;
- `evidence/plan018-v4-9-task5-lane-m-collection.txt` — eight exact critical
  real-store node IDs;
- `evidence/plan018-v4-9-task5.junit.xml` — 65-test fast-lane result;
- `evidence/plan018-v4-9-task5-lane-m.junit.xml` and sidecar — 22-test isolated
  MySQL result and attestation;
- `evidence/plan018-v4-9-task5-evidence.json` — image, counts, elapsed time,
  artifact hashes, and zero-external-effects attestations.

The fail-closed validator exits 0:

```bash
cd /home/taishajo/work/NExtSEEK-plan018-v4-9
python3 scripts/plan018_v4_9_task5_mutation.py validate
```

The complete reproducible run is:

```bash
cd /home/taishajo/work/NExtSEEK-plan018-v4-9
python3 scripts/plan018_v4_9_task5_mutation.py run
```

The runner pins app image digest
`sha256:704e0936c966a5e4121957104f236d111c251db0feb413aa2c8e8a5e3f7fa651`,
mounts the worktree at `/work`, sets
`DJANGO_SETTINGS_MODULE=dmac.test_settings`, and disables networking in the fast
lane. Only the established Lane M harness creates an isolated disposable Docker
network and MySQL database; its cleanup trap removes both.

## Verification development

The first complete test execution was green but the wrapper failed because the
host-absolute Lane M JUnit destination did not exist inside the `/work` mount.
The runner now translates repository artifacts to container paths, and a focused
regression test pins that behavior. A later validator audit deliberately made
the prior evidence RED by adding exact control/source/test key-set checks,
manifest-definition comparisons, sidecar validation, execution-count checks,
wrong-root validation, malformed-artifact refusal, and explicit deselection
accounting. After regeneration, the focused validator suite passes **8/8** and
the evidence validator reports PASS.

## External effects

No provider call, paid action, stored-evidence replay, MCMC fit, live database,
deployment, production enablement, registry write, reverse migration, or
retained-data deletion occurred. Lane M used only a disposable MySQL container
on an isolated Docker network.

## Authorization state

Task 4 was pushed to `origin/dev` at `1c461f7d` before Task 5 began. Task 5 was
authorized for local implementation. No Task-5 push, Task-6 scope, deployment,
provider spend, live DB action, or registry write has been authorized.

## Task-7 recovery integration — 2026-08-18

The mutation inventory now includes the three Task-7 executable guard-removal
mutants: exact runtime-identity bypass, contract-phase acceptance, and
destructive-recovery acceptance. The source-bound total is **76/76 KILLED**.
The fast collection is **68/68 passed in 3.20 seconds** and the unchanged
disposable-MySQL collection is **22/22 passed in 89.58 seconds**. The complete
gate passed in **167.693 seconds**, well below its 900-second cap.

Both application lanes now invoke the pinned image environment through
`uv run --project /app --no-sync`. The fast container is capped at 2 CPUs and
4 GiB; Lane M caps the application container at 2 CPUs/4 GiB and MySQL at 2
CPUs/2 GiB. The Lane-M launcher itself is now hash-bound as a Task-5 control.
No provider, paid, live-database, deployment, registry, MCMC, stored-evidence,
reverse-migration, or retained-data operation occurred.
