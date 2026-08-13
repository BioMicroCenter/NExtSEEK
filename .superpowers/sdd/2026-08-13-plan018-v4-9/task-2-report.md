# Plan 018 V4-9 Task 2 report

## Outcome

Task 2 is complete as a hermetic coverage gate: the source-derived paired producer,
schema/export, attempt-storage, DD-44 judgment, disposition, conservation, and Stage-C
cluster passes the required per-module and aggregate 95% statement and branch floors.

## Scope derivation

The critical cluster is the intersection of Task 2's nouns with existing coverage-bearing
V4-2/V4-3 ownership entries in `evidence/plan018-v4-9-owned-surface.json`, checked against
the V4-2/V4-3, V5-3 §2, and V9 clauses of the living plan. It contains:

- `nessie_tests/bayes_manifest.py`, `bayesian.py`, and `export.py`;
- `nextseek_api/eval/{human_annotations,conservation,disposition,judge,judge_models,attempt_store,stage_c_runner}.py`.

Fitter/store/activation/router/monitoring/spend modules belong to Tasks 3/4. The owned-surface
manifest marks `artifact_sources.py` and `artifact_validity.py` as declared absent, so this task
does not pretend to cover modules that do not exist.

## RED → GREEN evidence

1. `scripts/test_plan018_v4_9_task2_coverage.py` was written before
   `scripts/plan018_v4_9_task2_coverage.py`; the first network-denied lane run failed because the
   gate module was absent. After the minimal evaluator was implemented, it passed (3 focused tests).
2. The initial real branch measurement exposed below-floor defensive branches in attempt storage,
   conservation, annotations, DD-44 rationale, Stage-C replay, producer resume, and export.
3. Tests were added for observable fail-closed outcomes. The only test doubles simulate an
   inconsistent internal/dependency contract (corrupt payload reader, corrupt vocabulary, corrupt
   bucket); each asserts the safe outcome, never merely executes a line. No production module was
   changed.

## Verification

All product/Django tests used the required Docker worktree mount plus `dmac.test_settings`:

```bash
docker run --rm --network none \
  -v /home/taishajo/work/NExtSEEK-plan018-v4-9:/repo -w /repo \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  -e PYTHONPATH=/repo:/repo/dmac_assistant/src \
  nextseek-nextseek:latest \
  uv run --project /app --no-sync python -m coverage run --branch ... -m pytest ...
```

The accumulated bounded coverage collection selected **250** tests: **248 passed, 2 skipped,
0 xfailed, 93 deselected**. The two skips are pre-existing root-only unreadable-file tests in
`nessie_tests/tests/test_export.py`; no unexpected skip/xfail occurred. The two Bayesian chunks
are complementary 16-test partitions and together cover the complete named Bayesian module.

Focused final regression command:

```bash
docker run --rm --network none ... uv run --project /app --no-sync python -m pytest \
  scripts/test_plan018_v4_9_task2_coverage.py \
  nextseek_api/eval/tests/test_v4_9_task2_behavior.py \
  nextseek_api/eval/tests/test_stage_c_runner.py \
  nextseek_api/eval/tests/test_judge_aggregation.py \
  nextseek_api/eval/tests/test_conservation.py \
  nessie_tests/tests/test_bayesian.py -q -p no:cacheprovider \
  -k 'v4_9 or completed_arms or replay_refuses or rationale_falls or corrupt_non_scored'
```

Result: **15 passed, 61 deselected**, exit 0. `git diff --check` was also clean.

The source-bound evaluator writes:

- `evidence/plan018-v4-9-task2-coverage.raw.json` — coverage.py branch data;
- `evidence/plan018-v4-9-task2-coverage.json` — machine-checked floor result;
- `evidence/plan018-v4-9-task2-evidence.json` — source hashes, exact counts, no-network proof,
  and fault-injection rationale.

Final aggregate coverage: **98.8% statements (1082/1095), 96.9% branches (345/356)**. Each
named module clears both floors; per-module values and SHA-256 source identities are recorded in
the evidence JSON.

## External-effects confirmation and limitations

Every test/coverage collection used `docker --network none` and synthetic evaluator/HTTP fakes;
no paired producer, route execution, provider/network call, paid action, live DB, deployment,
registry action, or production change occurred. The two root-only skipped export tests remain
intentionally skipped; their behavior is unrelated to Task 2's network/producers and is disclosed
in the collection accounting.

## Files changed

- Coverage gate and gate tests: `scripts/plan018_v4_9_task2_coverage.py`,
  `scripts/test_plan018_v4_9_task2_coverage.py`.
- Behavioral/fault tests: `nextseek_api/eval/tests/test_v4_9_task2_behavior.py` and narrow
  additions to existing producer, conservation, DD-44, and Stage-C tests.
- Final reproducible evidence: the three Task-2 evidence JSON files above.

## Commit

Implementation commit: `4b61e9590681c63b4af6d4ee255ac2bf6905eb3d` —
`test(plan018-v4-9): gate task 2 branch coverage`. This report is committed in the
immediately following local documentation commit so the implementation SHA is stable.

## Fix round 1

The original cluster list was replaced with the pinned
`evidence/plan018-v4-9-task2-ownership.json` task-to-path mapping. It includes the previously
omitted existing Task-2 export/schema paths and assigns fitter/store/spend/paired-boundary paths to
Tasks 3/4; the two declared-absent artifact modules are explicit rather than silently omitted.
Task-2 controls/evidence are now manifest controls, and
`python3 scripts/plan018_v4_9_owned_surface.py validate --current` passes after regeneration.

The coverage validator now derives its set from that mapping, rejects invalid/zero/impossible
counters, and applies the 95% floor with integer arithmetic before display rounding. The DD-44
corrupt no-match rationale case now fails closed with `ValueError` rather than returning a
contradictory first rationale. Fresh focused verification: 16 passed; owned-surface generation and
current validation passed. The previously recorded ten-module coverage evidence is retained as
historical evidence only and must be regenerated for the expanded mapped cluster before a final
Task-2 PASS claim.
