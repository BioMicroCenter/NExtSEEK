# Plan 018 V4-9 Task 6 report

## Outcome

**Status: PASS as of 2026-08-18.** The bounded acceptance replay authenticated
all transferred delivery containers before parsing them, retained all 149
pairs/298 arms, exercised the real attempt store and Stage C aggregation with
exactly three stored judgments for each of 274 eligible arms, ran the
pair-preserving initial-human-grade quality MCMC fit, published and locally
activated its candidate in isolated in-memory SQLite, and exercised posterior
routing and fallback. The final one full-chain test passed in **46.60 seconds**;
the complete image-build/evidence gate passed in **48.996 seconds**, below its
300-second cap.

The authenticated delivery identities were:

- `testquestions.zip`: 66,473,692 bytes,
  `4e7c57a1c04015fbbe4696302d258038b72e71b1bedb17866810474ac74cb814`;
- `MANIFEST.json`: 2,375,457 bytes,
  `d14cb4b153448e295110f3bfdbc5004f1e0455e0673ebcac15ecfe9d635227c2`;
- `artifact_validity_set3_final.csv`: 44,853 bytes,
  `7d8859bd206d1c932773cc1d2d0791341a3eb54bdbecc32ea250a58f1827f693`.

The replay produced 822 stored attempts. It conserved 149 retained pairs and
298 arms with zero excluded or pending pairs. The activated local candidate
routed `graph_traversal` to NS and `unsupported` to CC; indecisive
`sample_search` used the legacy fallback.

## Provenance boundary

The transferred archive contains authenticated human functional-grade files;
it does **not** contain a historical three-attempt provider-judgment store. The
acceptance harness therefore materializes deterministic stored
`authenticated_human_grade_acceptance_oracle` records solely to exercise the
DD-44 storage, hash-verification, retrieval, and aggregation seam. It verifies
that every eligible arm's three-record aggregate agrees with the authenticated
human grade.

Those 822 records are not represented as historical provider output, are not
used as independent fit authority, and do not establish that 822 provider
judgments existed in the transfer. The result explicitly records
`historical_provider_judgments_claimed: false` and `provider_calls: 0`. The
fit's functional-success authority remains the authenticated human grades and
its publication authority remains `provisional_initial_human_grade`.

## Reproducible verification

The composite image is built without network access from two exact local
images: application image
`sha256:704e0936c966a5e4121957104f236d111c251db0feb413aa2c8e8a5e3f7fa651`
and evaluation image
`sha256:0045e7dbb3d020865cf76e92ab3eebecfc176558ec4a999a7a8b9bfed8d961ab`.
The test container has networking disabled and is capped at 2 CPUs and 4 GiB.

```bash
cd /home/taishajo/work/NExtSEEK-plan018-v4-9
python3 scripts/plan018_v4_9_task6_replay.py run
python3 scripts/plan018_v4_9_task6_replay.py validate
```

Machine-checkable evidence:

- `evidence/plan018-v4-9-task6-replay.json` — exact sources, conservation,
  stored-attempt provenance, fit, publication, activation, and routing result;
- `evidence/plan018-v4-9-task6.junit.xml` — exact one-test execution;
- `evidence/plan018-v4-9-task6-evidence.json` — image identities, control and
  artifact hashes, resource/network boundaries, elapsed time, and zero external
  effects.

The focused fail-closed gate suite passes 5/5. It covers missing evidence,
wrong evidence identity, current control inventory, missing output artifacts,
and JUnit records that omit source identity. The final evidence validator exits
0 only while the delivery, controls, result artifacts, exact JUnit node, image
identities, source hashes, counts, fit authority, routing, and external-effects
attestations remain current.

## External effects and authorization state

No new paired route was executed. No provider or paid call, network access,
live database, deployment, production enablement, or remote registry write
occurred. Task 5 was pushed to `origin/dev` at `1a9c940c` before Task 6 began.
Task 6 was authorized for local implementation; its push and Task 7 scope have
not yet been authorized.
