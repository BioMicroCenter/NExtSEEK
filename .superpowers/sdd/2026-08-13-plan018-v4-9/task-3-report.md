# Plan 018 V4-9 Task 3 report

## Outcome

**Status: PASS as of 2026-08-17.** The authenticated deployed-image coverage lane
selected **142 exact tests**: 142 passed, 0 failed, 0 errors, 0 skipped, 0 xfailed,
and 0 deselected in **3.25 seconds**. Across the thirteen declared critical
modules, coverage is **1076/1076 statements (100.0%)** and **336/338 branches
(99.4%)**. Every module independently clears both 95% floors; the lowest is
`fit_boundary.py` at 100.0% statements and 95.5% branches.

The disposable MySQL 8.0 Lane M separately passed **12/12** real-store tests with
zero failures, errors, skips, or xfails under `REPEATABLE-READ`. It exercises stale
CAS, two-activator contention, immutable overwrite refusal, rollback, concurrent
snapshot reads, parent mismatch, content and canonical-payload corruption,
taxonomy/corpus incompatibility, partial publication refusal, and both publish and
activation crash boundaries.

Machine-checkable evidence:

- `evidence/plan018-v4-9-task3-ownership.json` — exact critical set and explicit exclusions;
- `evidence/plan018-v4-9-task3-coverage.json` — per-module and aggregate floors;
- `evidence/plan018-v4-9-task3-evidence.json` — source/test hashes, image, collection, JUnits, and Lane M hashes;
- `evidence/plan018-v4-9-task3-lane-m.sidecar.json` — disposable MySQL identity and oracle list.

The fail-closed validator exits 0:

```bash
cd /home/taishajo/work/NExtSEEK-plan018-v4-9
python3 scripts/plan018_v4_9_task3_coverage.py validate
```

The complete reproducible run is:

```bash
cd /home/taishajo/work/NExtSEEK-plan018-v4-9
python3 scripts/plan018_v4_9_task3_coverage.py run
```

That runner pins the deployed app image digest
`sha256:704e0936c966a5e4121957104f236d111c251db0feb413aa2c8e8a5e3f7fa651`,
mounts the current worktree at `/work`, sets `DJANGO_SETTINGS_MODULE=dmac.test_settings`,
and denies network access for the coverage lane. It then invokes the established
disposable MySQL harness with `dmac.test_settings_realstack`; the harness removes
its test container and isolated network on exit.

## Scope and test design

The critical set follows the Task-3 assignment map and the plan's explicit V14
nouns: `fit_boundary.py`, all nine executable `fit/v14` modules, generation store,
generation validation, and publication. Package initializers contain no executable
statements. Pre-V14 code under `fit/vendor/` is inherited vendored tooling, not V14
runtime logic. `human_grade_fit.py` is outside the accepted Task-3 assignment map;
its full authenticated 149-pair replay is a Task-6 oracle and is not silently
counted in this PASS.

The new tests are behavior-focused and hardware-bounded. The statistical backend
is replaced only at the NumPyro/JAX boundary to exercise orchestration, diagnostics,
fallback, censoring, and error branches deterministically. No test performs warmup
or posterior sampling. Those doubles are explicitly not evidence that the model is
statistically correct; the previously authenticated V4-4 real-MCMC acceptance
remains the statistical oracle.

The coverage oracle has its own adversarial tests: 94.9% cannot round to PASS,
missing modules fail, non-branch collection fails, and the exact 95.0% boundary
passes. Evidence validation binds every production module, every selected test
source, the exact collection, both JUnits, the raw coverage JSON, the owned-surface
manifest, and the Task-3 ownership declaration by SHA-256.

## External effects

No provider call, paid action, live database, deployment, production enablement,
registry write, reverse migration, or retained-data deletion occurred. The only
network-enabled resources were an isolated disposable Docker network and disposable
MySQL database used by Lane M; both were removed by the established harness.

## Authorization state

Task 3 is committed locally only. No Task-3 push has been authorized. Proceeding to
Task 4 and pushing the Task-3 commit each require an explicit maintainer decision.

Implementation and authenticated evidence commit:
`a0f0defc` — `test(plan018-v4-9): gate task 3 coverage`. This report is committed
immediately afterward so that implementation identity remains stable.
