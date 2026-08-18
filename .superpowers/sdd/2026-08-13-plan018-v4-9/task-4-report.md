# Plan 018 V4-9 Task 4 report

## Outcome

**Status: PASS as of 2026-08-18.** The authenticated deployed-image coverage lane
selected **219 exact tests**: 219 passed, 0 failed, 0 errors, 0 skipped, 0 xfailed,
and 0 deselected in **4.36 seconds**. Across the seventeen declared critical
modules, coverage is **927/935 statements (99.1%)** and **208/210 branches
(99.0%)**. Every module independently clears both 95% floors. The lowest statement
result is `posterior_selector.py` at 96.0%; the lowest branch result is `router.py`
at 97.2%.

The disposable MySQL 8.0 Lane M separately passed **10/10** real-store tests with
zero failures, errors, skips, or xfails under `REPEATABLE-READ`. It exercises
four-process spend contention, idempotent replay, four crash boundaries, provider
exception release, broker redelivery, orphan release, and expiry sweeping.

The first Lane M run was RED: four workers concurrently replaying the same valid
manifest exposed a check-then-insert race in `approve_run_manifest()`. The unique
constraint rejected the losing insert with `IntegrityError`, but the function did
not reload the identical winner. The fix wraps each manifest insert in a savepoint
and, after a losing unique race, reloads only an identical, unconsumed manifest;
collision and consumed-manifest refusals remain fail-closed. Deterministic unit
tests cover this path for both approval entrypoints, and the real four-process
oracle passed on rerun.

Machine-checkable evidence:

- `evidence/plan018-v4-9-task4-ownership.json` — exact critical set, authority, and explicit exclusions;
- `evidence/plan018-v4-9-task4-coverage.json` — per-module and aggregate floors;
- `evidence/plan018-v4-9-task4-evidence.json` — source/test hashes, image, collection, JUnits, and Lane M hashes;
- `evidence/plan018-v4-9-task4-lane-m.sidecar.json` — disposable MySQL identity and ten-oracle list.

The fail-closed validator exits 0:

```bash
cd /home/taishajo/work/NExtSEEK-plan018-v4-9
python3 scripts/plan018_v4_9_task4_coverage.py validate
```

The complete reproducible run is:

```bash
cd /home/taishajo/work/NExtSEEK-plan018-v4-9
python3 scripts/plan018_v4_9_task4_coverage.py run
```

The runner pins app image digest
`sha256:704e0936c966a5e4121957104f236d111c251db0feb413aa2c8e8a5e3f7fa651`,
mounts the current worktree at `/work`, sets
`DJANGO_SETTINGS_MODULE=dmac.test_settings`, and denies network access for the
coverage lane. It then invokes the established disposable MySQL harness with
`dmac.test_settings_realstack`; the harness removes its test container and
isolated network on exit.

## Scope and test design

The critical set follows the accepted V4-0 ownership map, the explicit Task-2
assignment of `paired_run.py` and paid-run controls to Task 4, and the V4-6,
V4-7, and V4-8 sidecars. It includes dynamic label introspection, classifier and
router Python seams, selector/fallback, transport trace and ledger, monitoring and
the experimental/observational boundary, and all runtime manifest/reservation/
provider/reconciliation/refusal modules.

The tests are hardware-bounded: the coverage lane finishes in seconds. Provider
behavior uses in-memory fakes behind a network-disabled container. Database tests
use Django's disposable database; only Lane M creates a disposable MySQL container
and isolated Docker network. No statistical fitting or benchmark replay occurs.

The coverage oracle has adversarial tests: 94.9% cannot round to PASS, missing
modules fail, non-branch collection fails, and exact 95.0% passes. Validation binds
every production module, every selected test source, the exact collection, both
JUnits, raw coverage, the owned-surface manifest, and Task-4 ownership by SHA-256.

## External effects

No provider call, paid action, live database, deployment, production enablement,
registry write, reverse migration, or retained-data deletion occurred. The only
network-enabled resources were an isolated disposable Docker network and MySQL
database used by Lane M; both were removed by the established harness.

## Authorization state

Task 3 was pushed to `origin/dev` at `946479b7` before Task 4 began. Task 4 is
committed locally only; no Task-4 push, Task-5 scope, deployment, provider spend,
live DB action, or registry write has been authorized.

Implementation, concurrency fix, tests, and authenticated evidence commit:
`a2a40e88` — `test(plan018-v4-9): gate task 4 coverage`. This report is committed
immediately afterward so the implementation identity remains stable.
