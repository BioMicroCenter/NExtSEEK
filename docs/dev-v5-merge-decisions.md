# dev-v5-merge: merge decisions

Base `origin/dev` @ `809c29b7`, merged `origin/dev-v4-merge` @ `a9de92bd`
(merge commit `8b20d4db`, tree `4bcfb26e`, zero conflicts, zero migration
divergence). Five known dev-side defects were triaged before merging; four
are fixed as separate commits on top of the merge. This records the fifth.

## 4. route_capabilities.json — no change on this branch, gated before deploy

**Decision: ship dev's generated file as committed. Change nothing here, and
change nothing in nessie_tests.**

### What was considered and rejected

*Pinning the pre-merge file* was rejected on measurement. That file predates
the 2026-08-04 28-family remap: 11 of its 15 family names do not exist in
`nessie_tests/corpus.json`, and it covers the `route_policy` contract by
1 of 13 families. dev's file covers 13 of 13 and introduces no new names.
Pinning would restore a vocabulary the test harness cannot reference, and
would additionally red `test_check_surfaces_passes_on_current_tree`, since
Plan 005 generates this file and drift-checks it.

*Teaching the generator to honour `route_policy`* was rejected because the
decoupling is deliberate and pinned by
`nextseek_api/assistant/tests/test_route_capabilities.py::test_route_policy_and_plugin_json_do_not_move_families`,
which mutates a `route_policy` entry and asserts the generated families do
not move. Changing that is a design reversal, not a bugfix, and belongs with
the taxonomy owner.

*Updating nessie_tests to dev's families* was rejected as a no-op: there is
no vocabulary gap. `corpus.json` is byte-identical on both branches and every
one of dev's 25 families is already in its 28-family taxonomy.

### The open concern, stated precisely

The disagreement is about route assignment, not names.
`build_tools/gen_op_surfaces/route_capabilities.py` assigns a family to a
route when that arm *succeeded* on it (`select_family_examples` ->
`recompute_arm_success`), sourced from the 149 paired human-graded records in
`op_registry/route_example_evidence.json` — the same bundle the HiBayes
posterior fit uses. Because Container-CC succeeded wherever NExtSEEK did,
`nextseek_query`'s families are now a strict subset of `container_cc`'s:

- 19 families appear under both routes, with byte-identical descriptions
- 27 example queries are listed under both routes
- all 8 families `route_policy` pins to `nextseek_query` with `op: eq` are
  advertised under `container_cc` as well

`router.baml:65-71` renders task families under each route heading, so the
model sees the same family, description and examples twice. The remaining
discriminator is the route-level `Best for:` / `Avoid for:` prose, which is
still specific and may well carry routing on its own. Note also that
`container_cc.not_for` says nf-core pipeline launch "belongs on the NS route"
while `pipeline_launch` is listed under both.

**This is a prompt ambiguity, not an observed misroute.** Nothing here is
demonstrated to route incorrectly.

### Gate

Run the nessie_tests **route tier** against this branch before any deploy. It
is the cheap, unpaid tier and it asserts `route_policy` against real routing
decisions, which is exactly the question above. Treat its result as the
arbiter:

- pinned families still route NExtSEEK -> dev's file is fine, close this out
- drift toward Container-CC -> a measured defect; decide then whether to fix
  the generator or amend the ruling, with data

### Where this really belongs

This is the same taxonomy problem blocking the HiBayes posterior router, seen
from the other side. `ClassifyQuery` receives family names and one-line
descriptions only (`family_labels.py:87-90`), while `RouteQuery` receives the
same families with example queries — so the classifier meant to replace the
router runs on a strictly poorer prompt, which is the likeliest cause of its
3-of-5 paid E2E misclassifications. One family-boundary ruling fixes the
router prompt and the classifier together. Do it there, not here.
