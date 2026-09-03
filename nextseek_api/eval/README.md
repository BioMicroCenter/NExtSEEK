# `nextseek_api/eval/`

## What this is

The HiBayes evaluation pipeline. It decides, with a stated uncertainty, which of the two
assistant engines should answer a given task family, and makes that decision durable,
auditable and reversible.

The work is a ladder. Paired run evidence — the same corpus question answered once by
`nextseek_query` and once by `container_cc` — is graded arm by arm; each arm lands in
exactly one disposition bucket; only retained scored pairs are admitted to a Bayesian
fit; the fit yields a per-family decision; the decision is published as an immutable
posterior generation. With one Django setting on, the live router reads the active
generation and lets it choose the route
(`nextseek_api/cc_assistant/router.py:308-312`).

This is a plain Python package, not a Django app. Searching the package for `apps.py`,
`urls.py`, `admin.py`, `views.py` or a `migrations` directory turns up only three
`models.py` files, all inside `fit/vendor/` and all three pydantic rather than Django —
`nextseek_api/eval/fit/vendor/hibayes_artifact_validity/models.py:10`,
`nextseek_api/eval/fit/vendor/hibayes_functional_usefulness/models.py:11` and
`nextseek_api/eval/fit/vendor/hibayes_runtime_reliability/models.py:20`; grepping
`dmac/settings.py`, `dmac/test_settings.py`, `dmac/urls.py` and `nextseek_api/urls.py`
for the string `nextseek_api.eval` returns nothing, so nothing installs it as an app or
mounts a route for it. Its ORM tables belong to a sibling package instead.

Counted 2026-09-03, the package holds 105 Python files: 52 modules of product and
operator code, 34 under `nextseek_api/eval/tests/`, and 19 vendored under
`nextseek_api/eval/fit/vendor/`. The package docstring frames the whole thing as one
plan increment (`nextseek_api/eval/__init__.py:1`).

## Surface

**What "surface" means here.** This is a Python package with two kinds of entry point:
importable functions that other packages call, and `argparse` mains an operator runs by
hand. The edge is therefore imports in both directions — derived below by grepping the
whole tree rather than from memory — plus a handful of files reached by absolute path
instead of by import, which are called out separately because a path is not an import.

**Rows, dispositions and conservation.** `nextseek_api/eval/router_models_proposal.py:1`
holds the eval row and its enums; `nextseek_api/eval/disposition.py:1` maps every arm
into a bucket, and its `should_call_judge` refuses to spend on any arm that is excluded
or that failed at runtime or artifact level
(`nextseek_api/eval/disposition.py:69-74`). `nextseek_api/eval/conservation.py:1` is the
accounting layer, whose `build_fit_admission` emits only retained scored pairs
(`nextseek_api/eval/conservation.py:165-171`). Human labels enter through
`nextseek_api/eval/human_annotations.py:1`.

**Evidence kinds.** Two schemas, deliberately kept apart:
`nextseek_api/eval/paired_run.py:1` for experimental batches and
`nextseek_api/eval/online_observation.py:1` for live-traffic rows, discriminated by the
constants at `nextseek_api/eval/evidence_kinds.py:17-23`. Approval of a paired run is
registry-backed (`nextseek_api/eval/paired_run_registry.py:1`), and
`nextseek_api/eval/export.py:1` turns ledger rows into observational rows.
`nextseek_api/eval/fit/fit_boundary.py:1` is the wall between them.

**The judge.** `nextseek_api/eval/judge_models.py:1` mirrors the BAML evaluator schemas,
`nextseek_api/eval/judge.py:1` holds the aggregation operators, and
`nextseek_api/eval/stage_c_runner.py:1` runs exactly three evaluations per arm.
Judgments are content-addressed in `nextseek_api/eval/attempt_store.py:1` and
fingerprinted for reuse in `nextseek_api/eval/judge_cache.py:1`.
`nextseek_api/eval/judging_engine.py:1` wires the runner to the spend gate, with
`nextseek_api/eval/fake_provider.py:1` standing in for a real provider offline.

**Paid-run authorization.** A manifest is approved once
(`nextseek_api/eval/run_manifest.py:1`), budget is reserved atomically against it
(`nextseek_api/eval/run_authorization.py:1`), and every provider call goes through
`guarded_provider_call`, which reserves, requires, calls, then reconciles or releases
(`nextseek_api/eval/provider_gate.py:33-67`). Resume state lives in
`nextseek_api/eval/paid_run_state.py:1`, the arithmetic in
`nextseek_api/eval/spend_conservation.py:1`, the post-run artifact in
`nextseek_api/eval/reconciliation.py:1`, and
`nextseek_api/eval/paid_run_schedule.py:13-17` exists to refuse a scheduled entry into
the paid lane. `nextseek_api/eval/seam_inventory.py:1` walks the package's own AST to
list every provider seam and flag any that is not gated
(`nextseek_api/eval/seam_inventory.py:159-165`).

**The fit.** `fit/v14/` is the pair-preserving fitter
(`nextseek_api/eval/fit/v14/__init__.py:1`). Its parts:

| Concern | Where |
|---|---|
| Fingerprinted fit and decision config | `nextseek_api/eval/fit/v14/fit_config.py:1` |
| Fit input rows that keep pair identity | `nextseek_api/eval/fit/v14/pair_rows.py:1` |
| Quality multinomial over four joint states | `nextseek_api/eval/fit/v14/quality_model.py:1` |
| Paired robust latency model with censoring | `nextseek_api/eval/fit/v14/latency_model.py:1` |
| Decision contract and complete-set FDR | `nextseek_api/eval/fit/v14/decision.py:1` |
| Orchestration of fit plus decision | `nextseek_api/eval/fit/v14/combined.py:1` |
| Frozen validation matrix and its runner | `nextseek_api/eval/fit/v14/recovery_matrix.py:1` |
| Acceptance predicates over that matrix | `nextseek_api/eval/fit/v14/recovery_acceptance.py:1` |

**The generation store, and the live seam.** `nextseek_api/eval/generation_store.py:1` is
the immutable store plus a compare-and-swap pointer:
`nextseek_api/eval/generation_store.py:252` reads the active snapshot,
`nextseek_api/eval/generation_store.py:295` swaps it,
`nextseek_api/eval/generation_store.py:377` rolls it back, and
`nextseek_api/eval/generation_store.py:410` pins one to a turn.
`nextseek_api/eval/generation_validation.py:1` gates activation and
`nextseek_api/eval/publish.py:1` builds the manifest that is published.

**Operator command lines.** Four modules have an `argparse` main rather than an
importable-only surface:
- `nextseek_api/eval/human_grade_fit.py:1113-1121` — the authenticated human-grade fit,
  whose `--action` chooses dry-run, publish or activate.
- `nextseek_api/eval/judge_human_compare.py:1-5` — compares judge output to human grades,
  zero-provider unless `--execute-provider` is passed.
- `nextseek_api/eval/functional_inputs.py:1-6` and `nextseek_api/eval/exporter.py:1-11` —
  the two CSV builders that feed the vendored HiBayes axes.
- `nextseek_api/eval/fit/v14/recovery_runner.py:1` — runs the frozen recovery matrix.

**Replay and deployment harnesses.** `nextseek_api/eval/v4_3_verifier.py:1` replays a
delivery without provider spend; `nextseek_api/eval/task6_replay.py:1-9` does the same
through local activation and selection, against the throwaway SQLite settings at
`nextseek_api/eval/task6_settings.py:10-15`.
`nextseek_api/eval/deploy_record.py:1-6` is the closed deployment identity, and
`nextseek_api/eval/mixed_version_recovery.py:1-7` the compatibility and recovery harness
over it.

**Modules that announce themselves as proposals, not product.** Two files say in their
own opening lines that they are runnable references rather than implementations:
`nextseek_api/eval/router_models_proposal.py:3-5` and
`nextseek_api/eval/artifact_validity_proposal.py:3-6`, the latter naming the module path
it reserves for the real implementation.

**Committed data.** Two CSVs sit at the package root and are the delivered set3 results:
`nextseek_api/eval/artifact_validity_set3_final.csv` carries 298 arm rows and
`nextseek_api/eval/artifact_detail_set3_final.csv` 256 artifact rows, both counted with
`wc -l` on 2026-09-03 and both matching the totals the generating module's docstring
states (`nextseek_api/eval/artifact_validity_proposal.py:55`).

**`fit/vendor/` is vendored third-party code** — three HiBayes analysis packages plus a
combined report renderer, carried in whole with their own configs and Jinja templates.
Treat the subtree as an upstream artifact and read its own documentation
(`nextseek_api/eval/fit/vendor/hibayes_runtime_reliability/README.md:1-9`) rather than
this file. Provenance for the ported, non-vendored modules is recorded inline at
`nextseek_api/eval/enums.py:1`.

## Running and testing

The lane that actually runs is a throwaway container over a **writable** copy of this
worktree, driven by the app image's own virtualenv. A read-only mount of a fresh
worktree cannot work, because Django settings create directories at import time
(`dmac/settings.py:497-499`). See `nextseek_api/eval/CLAUDE.md` for that exact command
and the run it produced.

The 34 failed and 17 errors recorded on 2026-09-03 are all environmental, and they fall
into three groups worth telling apart:

- 41 of them want an authenticated delivery directory that is absent from this machine.
  Two test modules hardcode its path
  (`nextseek_api/eval/tests/test_human_grade_fit.py:26`,
  `nextseek_api/eval/tests/test_v4_9_task6_replay.py:11`), and the fit refuses to
  proceed without it (`nextseek_api/eval/human_grade_fit.py:249-254`).
- 8, spread over `nextseek_api/eval/tests/test_v4_8_mysql.py` and
  `nextseek_api/eval/tests/test_generation_store_mysql.py`, want a migrated real store.
  They have a lane of their own: `scripts/plan018_lane_m_mysql.sh:19` names the first of
  those files as its default target, stands up a disposable MySQL, migrates, and runs it
  under `dmac.test_settings_realstack` (`scripts/plan018_lane_m_mysql.sh:53`).
- 2, in `nextseek_api/eval/tests/test_v14_quality_hierarchical.py`, want the sampler
  stack that only a different image carries.

The Bayesian fit needs an image this one is not. `docker/eval/Dockerfile:12-14` installs
the JAX, NumPyro and ArviZ stack and copies only this package in
(`docker/eval/Dockerfile:10`); `docker/eval-task6/Dockerfile:11-14` then grafts the app
image's Django into it for the replay harness. Neither image is named in
`docker-compose.yml`, which is why both are built by hand.

Nothing here touches Neo4j: grepping every file under `nextseek_api/eval` for `neo4j`,
case-insensitively, returns nothing, so the fake-but-configured Neo4j at
`dmac/test_settings.py:51-55` never comes into play in this package's tests.

There is no `conftest.py` and no `host_only` marker anywhere in this package: a `find`
for a file named `conftest.py` beneath `nextseek_api/eval` returns nothing, and grepping
every file under `nextseek_api/eval` for the string `host_only` also returns nothing. The
marker declared at `pyproject.toml:148` therefore selects none of these tests.

## Depends on / depended on by

Depends on, outside this directory:

- `nextseek_api/assistant/models_db.py` for every ORM table written here. Seven modules
  import it at module scope, counted 2026-09-03: `nextseek_api/eval/export.py:4`,
  `nextseek_api/eval/generation_store.py:14`,
  `nextseek_api/eval/generation_validation.py:6`, `nextseek_api/eval/judge_cache.py:7`,
  `nextseek_api/eval/paid_run_state.py:7`, `nextseek_api/eval/run_authorization.py:14`
  and `nextseek_api/eval/spend_conservation.py:9`.
- See `nextseek_api/assistant/README.md` and `nextseek_api/assistant/CLAUDE.md` for what
  those tables are and who else writes them.
- Django's ORM and transaction machinery at module scope in four modules —
  `nextseek_api/eval/generation_store.py:11-12`,
  `nextseek_api/eval/paid_run_state.py:4-5`,
  `nextseek_api/eval/run_authorization.py:10-12` and
  `nextseek_api/eval/spend_conservation.py:7` — so importing any of them without
  configured settings raises.
- `nextseek_api/cc_assistant/family_labels.py` for the corpus taxonomy and hash, at
  module scope in `nextseek_api/eval/human_grade_fit.py:24` and inside a function body at
  `nextseek_api/eval/generation_validation.py:125`.
- `nextseek_api/cc_assistant/posterior_selector.py`, imported inside the replay driver at
  `nextseek_api/eval/task6_replay.py:175`. This is the return leg of a cycle, described
  below.
- `dmac_assistant`'s generated BAML client, imported only after the provider flag is set
  (`nextseek_api/eval/judge_human_compare.py:480-481`). The vendoring guard checks for
  `dmac_assistant.eval` and `tools.hibayes`, not for the router client
  (`nextseek_api/cc_assistant/tests/test_eval_vendoring.py:20`), so this import is
  permitted by design.
- JAX, NumPyro and ArviZ, imported lazily inside the fit functions
  (`nextseek_api/eval/fit/v14/quality_model.py:82-84`,
  `nextseek_api/eval/fit/v14/latency_model.py:75-78`) and supplied only by
  `docker/eval/Dockerfile:12-14`.
- An authenticated delivery directory that is not in this repo, pinned by SHA-256 for
  three container files and six archive members
  (`nextseek_api/eval/human_grade_fit.py:107-119`).

Read by absolute path rather than imported, which is a different kind of edge:

- `nextseek_api/cc_assistant/router.py`, bound as a module-scope constant at
  `nextseek_api/eval/seam_inventory.py:21` and rebound at
  `nextseek_api/eval/seam_inventory.py:125`, then parsed as AST. Nothing is imported from
  it, so a rename of that file breaks the scan without any import error.

Depended on by. Grepping every `.py` file in the tree for a dotted import of this package
on 2026-09-03, then removing the package's own internal imports, yields exactly 29 files.
They group as:

- Production code: exactly three modules, all in the Container-CC package —
  `nextseek_api/cc_assistant/posterior_selector.py:9`,
  `nextseek_api/cc_assistant/risk_overlay.py:25` and
  `nextseek_api/cc_assistant/route_monitoring.py:9`. No other non-test module anywhere
  outside `nextseek_api/eval/` and `scripts/` imports this package.
- Mutual coupling, not a one-way edge. The selector reads this package's active
  generation, and the router calls the selector at
  `nextseek_api/cc_assistant/router.py:281`; the replay driver here imports that same
  selector back at `nextseek_api/eval/task6_replay.py:175`, closing the loop.
- See `nextseek_api/cc_assistant/README.md` for the other end of that cycle and the rest
  of the routing surface.
- Nine one-off verification scripts, all named for the plan increment they close, from
  `scripts/plan018_v4_3_verifier.py:2` through `scripts/plan018_v4_9_task8_deploy.py:2`.
  They are drivers, not product code, and they write their JSON output into the
  repo-root `evidence/` tree.
- 17 test modules, every one of them under `nextseek_api/cc_assistant/tests/`, including
  the vendoring guard at `nextseek_api/cc_assistant/tests/test_eval_vendoring.py:16`. No
  test directory anywhere else in the repo imports this package.

Excluded from that list, and why: this package's own 34 test modules and its internal
cross-imports, which a naive tree-wide grep counts as consumers. Also excluded are the
matches for `nextseek_api/evaluator/`, which is a different subsystem — a URL prefix for
the admin evaluator surface, appearing at `ci/routes.py:634`,
`nextseek_api/assistant/descriptions_evaluator.py:43` and
`nessie_tests/FAMILIES.json:9647`. None of those three files touches this package.

See `nextseek_api/eval/CLAUDE.md` for the invariants, the traps and the one command.
