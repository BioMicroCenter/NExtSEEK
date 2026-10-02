# `NessieAI/hibayes/`

## What this is

The HiBayes evaluation pipeline. It decides, with a stated uncertainty, which of the two
assistant engines should answer a given task family, and makes that decision durable,
auditable and reversible.

The work is a ladder. Paired run evidence (the same corpus question answered once by
`nextseek_query` and once by `container_cc`) is graded arm by arm; each arm lands in
exactly one disposition bucket; only retained scored pairs are admitted to a Bayesian
fit; the fit yields a per-family decision; the decision is published as an immutable
posterior generation. With one Django setting on, the live router reads the active
generation and lets it choose the route
(`NessieAI/router/router.py:313-317`).

This is a plain Python package, not a Django app. Searching the package for `apps.py`,
`urls.py`, `admin.py`, `views.py` or a `migrations` directory turns up only three
`models.py` files, all inside `fit/vendor/` and all three pydantic rather than Django:
`NessieAI/hibayes/fit/vendor/hibayes_artifact_validity/models.py:10`,
`NessieAI/hibayes/fit/vendor/hibayes_functional_usefulness/models.py:11` and
`NessieAI/hibayes/fit/vendor/hibayes_runtime_reliability/models.py:20`. Grepping
`dmac/settings.py`, `dmac/test_settings.py`, `dmac/urls.py` and `nextseek_api/urls.py`
for `NessieAI.hibayes` returns nothing, so nothing installs it as an app or mounts a
route for it. Its ORM tables belong to `nextseek_api` instead. The package docstring
frames the whole thing as one plan increment (`NessieAI/hibayes/__init__.py:1`).

## HiBayes lives in these places

HiBayes is not confined to this folder. One judge-schema change touches two files
together: `NessieAI/dmac_assistant/baml_src/functional_evaluator.baml` and the hand copy
`NessieAI/hibayes/judge_models.py` (guard: `NessieAI/tests/hibayes/test_judge_models_baml_parity.py`).

| Piece | Where | Why there |
|---|---|---|
| Router wrapper and telemetry | `NessieAI/router/` (`router.py`, `router_context.py`, `baml_introspect.py`, `transport_trace.py`, `turn_ledger.py`) | the top-level router |
| Posterior routing consumers | `NessieAI/router/` (`posterior_selector.py`, `risk_overlay.py`, `route_monitoring.py`, `family_labels.py`) | on the live routing path; they import this package, and this package imports `family_labels` back (`human_grade_fit.py`, `generation_validation.py`) |
| HiBayes engine | this folder; tests in `NessieAI/tests/hibayes/` | judge, fit, vendored runners, generation store, paid-run gate, export, publish, task 6 replay |
| Task 6 AppConfig | `NessieAI/hibayes/task6_app.py` | its settings module, `task6_settings.py`, sits beside it |
| Evidence ingester | `NessieAI/cc/op_registry/paired_evidence.py` and `NessieAI/cc/op_registry/route_example_evidence.json` | build-time op contract; one of the three frozen engine-to-harness imports (`NessieAI/CLAUDE.md` "Boundary") |
| Persistence | the nine `eval_*` models in `nextseek_api/assistant/models_db.py`; migrations `0011_turn_judgment`, `0012_posterior_generation`, `0013_family_posterior`, `0014_generation_activation_and_reservation`, `0016_paired_run_registry`, `0017_paid_run_state` in `nextseek_api/migrations/` | app label `nextseek_api` and migration ownership stay in the API |
| Paired harness | `NessieAI/tests/nessie_tests/` (`bayesian.py`, `bayes_manifest.py`, `v4_2_verifier.py`, the shared `export.py`, `collect.py` and `corpus.json`), `.claude/skills/nessie-bayes-report/`, `NessieAI/tests/nessie_tests/output_skill_bayesian/` | the paired run is a harness mode; `export.py` is also imported by `NessieAI/build_tools/gen_op_surfaces/route_capabilities.py` |
| Judge contracts | `NessieAI/dmac_assistant/baml_src/`: `functional_evaluator.baml` (`EvaluateFunctionalUsefulness`), `judge_router.baml` (`JudgeRouterAnswer`), `judge_ui.baml` (`JudgeUITranscript`); `classifier.baml` is shared with the router | they must compile in the one tree that holds `clients.baml` |
| Agent-image judge client | `NessieAI/docker/cc-runtime/tools/e2e/judge_runner.py`, generated at build time from `NessieAI/dmac_assistant/baml_src/` through the compose named context `dmac_assistant_baml` | the agent image build context; it holds no BAML copy of its own |
| Fit image | `NessieAI/docker/eval/` | JAX, NumPyro and ArviZ; copies this package plus what `human_grade_fit.py` imports from outside it |
| Task 6 image, verifier scripts, plan and SDD ledgers | `NessieAI/history/plan018/docker/eval-task6/`, `NessieAI/history/plan018/scripts/`, `NessieAI/history/plan018/2026-07-31-hibayes-eval-routing.md`, `NessieAI/history/plan018/sdd/` | closed plan, frozen |
| Design | `NessieAI/docs/2026-07-31-hibayes-eval-routing-design.md` | the live design of the loop |
| Kill switch | `NEXTSEEK_POSTERIOR_ROUTING_ENABLED` in `dmac/settings.py`, `docker/nextseek.env.example` and `startup/templates/nextseek.env.template` | off by default; no path coupling |

Not HiBayes, although it matches an "eval" grep: the retry-context evaluator in
`NessieAI/chat_nextseek/src/chat_nextseek/evaluator/`, its ViewSet
`nextseek_api/services/evaluator.py`, the normalizers and retry body in `NessieAI/ns/retry.py`,
and the `/nextseek_api/evaluator/` routes.

## Surface

**What "surface" means here.** This is a Python package with two kinds of entry point:
importable functions that other packages call, and `argparse` mains an operator runs by
hand. The edge is therefore imports in both directions (derived below by grepping the
whole tree rather than from memory), plus a handful of files reached by absolute path
instead of by import, which are called out separately because a path is not an import.

**Rows, dispositions and conservation.** `NessieAI/hibayes/router_models_proposal.py:1`
holds the eval row and its enums; `NessieAI/hibayes/disposition.py:1` maps every arm
into a bucket, and its `should_call_judge` refuses to spend on any arm that is excluded
or that failed at runtime or artifact level
(`NessieAI/hibayes/disposition.py:69-74`). `NessieAI/hibayes/conservation.py:1` is the
accounting layer, whose `build_fit_admission` emits only retained scored pairs
(`NessieAI/hibayes/conservation.py:165-171`). Human labels enter through
`NessieAI/hibayes/human_annotations.py:1`.

**Evidence kinds.** Two schemas, deliberately kept apart:
`NessieAI/hibayes/paired_run.py:1` for experimental batches and
`NessieAI/hibayes/online_observation.py:1` for live-traffic rows, discriminated by the
constants at `NessieAI/hibayes/evidence_kinds.py:17-23`. Approval of a paired run is
registry-backed (`NessieAI/hibayes/paired_run_registry.py:1`), and
`NessieAI/hibayes/export.py:1` turns ledger rows into observational rows.
`NessieAI/hibayes/fit/fit_boundary.py:1` is the wall between them.

**The judge.** `NessieAI/hibayes/judge_models.py:1` mirrors the BAML evaluator schemas,
`NessieAI/hibayes/judge.py:1` holds the aggregation operators, and
`NessieAI/hibayes/stage_c_runner.py:1` runs exactly three evaluations per arm.
Judgments are content-addressed in `NessieAI/hibayes/attempt_store.py:1` and
fingerprinted for reuse in `NessieAI/hibayes/judge_cache.py:1`.
`NessieAI/hibayes/judging_engine.py:1` wires the runner to the spend gate, with
`NessieAI/hibayes/fake_provider.py:1` standing in for a real provider offline.

**Paid-run authorization.** A manifest is approved once
(`NessieAI/hibayes/run_manifest.py:1`), budget is reserved atomically against it
(`NessieAI/hibayes/run_authorization.py:1`), and every provider call goes through
`guarded_provider_call`, which reserves, requires, calls, then reconciles or releases
(`NessieAI/hibayes/provider_gate.py:33-67`). Resume state lives in
`NessieAI/hibayes/paid_run_state.py:1`, the arithmetic in
`NessieAI/hibayes/spend_conservation.py:1`, the post-run artifact in
`NessieAI/hibayes/reconciliation.py:1`, and
`NessieAI/hibayes/paid_run_schedule.py:13-17` exists to refuse a scheduled entry into
the paid lane. `NessieAI/hibayes/seam_inventory.py:1` walks the package's own AST, and
the router's, to list every provider seam and flag any that is not gated
(`NessieAI/hibayes/seam_inventory.py:167-173`).

**The fit.** `fit/v14/` is the pair-preserving fitter
(`NessieAI/hibayes/fit/v14/__init__.py:1`). Its parts:

| Concern | Where |
|---|---|
| Fingerprinted fit and decision config | `NessieAI/hibayes/fit/v14/fit_config.py:1` |
| Fit input rows that keep pair identity | `NessieAI/hibayes/fit/v14/pair_rows.py:1` |
| Quality multinomial over four joint states | `NessieAI/hibayes/fit/v14/quality_model.py:1` |
| Paired robust latency model with censoring | `NessieAI/hibayes/fit/v14/latency_model.py:1` |
| Decision contract and complete-set FDR | `NessieAI/hibayes/fit/v14/decision.py:1` |
| Orchestration of fit plus decision | `NessieAI/hibayes/fit/v14/combined.py:1` |
| Frozen validation matrix and its runner | `NessieAI/hibayes/fit/v14/recovery_matrix.py:1` |
| Acceptance predicates over that matrix | `NessieAI/hibayes/fit/v14/recovery_acceptance.py:1` |

**The generation store, and the live seam.** `NessieAI/hibayes/generation_store.py:1` is
the immutable store plus a compare-and-swap pointer:
`NessieAI/hibayes/generation_store.py:252` reads the active snapshot,
`NessieAI/hibayes/generation_store.py:295` swaps it,
`NessieAI/hibayes/generation_store.py:377` rolls it back, and
`NessieAI/hibayes/generation_store.py:410` pins one to a turn.
`NessieAI/hibayes/generation_validation.py:1` gates activation and
`NessieAI/hibayes/publish.py:1` builds the manifest that is published.

**Operator command lines.** Four modules have an `argparse` main rather than an
importable-only surface:
- `NessieAI/hibayes/human_grade_fit.py:1113-1121`: the authenticated human-grade fit,
  whose `--action` chooses dry-run, publish or activate.
- `NessieAI/hibayes/judge_human_compare.py:1-5`: compares judge output to human grades,
  zero-provider unless `--execute-provider` is passed.
- `NessieAI/hibayes/functional_inputs.py:1-6` and `NessieAI/hibayes/exporter.py:1-11`:
  the two CSV builders that feed the vendored HiBayes axes.
- `NessieAI/hibayes/fit/v14/recovery_runner.py:1`: runs the frozen recovery matrix.

**Replay and deployment harnesses.** `NessieAI/hibayes/v4_3_verifier.py:1` replays a
delivery without provider spend; `NessieAI/hibayes/task6_replay.py:1-9` does the same
through local activation and selection, against the throwaway SQLite settings at
`NessieAI/hibayes/task6_settings.py:10-15`.
`NessieAI/hibayes/deploy_record.py:1-6` is the closed deployment identity, and
`NessieAI/hibayes/mixed_version_recovery.py:1-7` the compatibility and recovery harness
over it.

**Modules that announce themselves as proposals, not product.** Two files say in their
own opening lines that they are runnable references rather than implementations:
`NessieAI/hibayes/router_models_proposal.py:3-5` and
`NessieAI/hibayes/artifact_validity_proposal.py:3-6`, the latter naming the module path
it reserves for the real implementation.

**Committed data.** Two CSVs sit at the package root and are the delivered set3 results:
`NessieAI/hibayes/artifact_validity_set3_final.csv` (arm rows) and
`NessieAI/hibayes/artifact_detail_set3_final.csv` (artifact rows). Their row counts match
the totals the generating module's docstring states
(`NessieAI/hibayes/artifact_validity_proposal.py:55`).

**`fit/vendor/` is vendored third-party code**: three HiBayes analysis packages plus a
combined report renderer, carried in whole with their own configs and Jinja templates.
Treat the subtree as an upstream artifact and read its own documentation
(`NessieAI/hibayes/fit/vendor/hibayes_runtime_reliability/README.md:1-9`) rather than
this file. Provenance for the ported, non-vendored modules is recorded inline at
`NessieAI/hibayes/enums.py:1`.

## Running and testing

Tests are in `NessieAI/tests/hibayes/`. The command is the Django lane in
`NessieAI/tests/README.md`, over a **writable** copy of the checkout: Django settings
create directories at import time (`dmac/settings.py:507-508`).

The failures that lane shows are environmental, and they fall into three groups worth
telling apart:

- Most want an authenticated delivery directory that is absent from any machine but
  one. Two test modules hardcode its path
  (`NessieAI/tests/hibayes/test_human_grade_fit.py:26`,
  `NessieAI/tests/hibayes/test_v4_9_task6_replay.py:11`), and the fit refuses to
  proceed without it (`NessieAI/hibayes/human_grade_fit.py:249-254`).
- `NessieAI/tests/hibayes/test_v4_8_mysql.py` and
  `NessieAI/tests/hibayes/test_generation_store_mysql.py` want a migrated real store.
  Their lane script is archived with plan018:
  `NessieAI/history/plan018/scripts/plan018_lane_m_mysql.sh:19` names the first of
  those files as its default target, stands up a disposable MySQL, migrates, and runs it
  under `dmac.test_settings_realstack` (`NessieAI/history/plan018/scripts/plan018_lane_m_mysql.sh:53`).
- `NessieAI/tests/hibayes/test_v14_quality_hierarchical.py` wants the sampler stack
  that only a different image carries.

The Bayesian fit needs an image the app image is not. `NessieAI/docker/eval/Dockerfile:21-23`
installs the JAX, NumPyro and ArviZ stack, and `NessieAI/docker/eval/Dockerfile:10-19` copies
this package in, plus the router and harness files `human_grade_fit.py` needs from outside
it (`NessieAI/router/family_labels.py`, `NessieAI/paths.py`, the set3 manifest models and
`corpus.json`). Django is not in it, and `human_grade_fit.main()` sets Django up first, so
neither that CLI nor the publish step runs there. The archived
`NessieAI/history/plan018/docker/eval-task6/Dockerfile:11-14` grafted the app image's
Django into it for the replay harness. Neither image is named in `docker-compose.yml`,
which is why both are built by hand.

Nothing here touches Neo4j: grepping every file under `NessieAI/hibayes` for `neo4j`,
case-insensitively, returns nothing, so the fake-but-configured Neo4j at
`dmac/test_settings.py:51-55` never comes into play in this package's tests.

No test here carries the `host_only` marker (declared at `pyproject.toml:148`), so that
marker selects none of them.

## Depends on / depended on by

Depends on, outside this directory:

- `nextseek_api/assistant/models_db.py` for every ORM table written here (an allowed
  back-edge, `NessieAI/CLAUDE.md` "Boundary"). Seven modules import it at module scope:
  `NessieAI/hibayes/export.py:4`,
  `NessieAI/hibayes/generation_store.py:14`,
  `NessieAI/hibayes/generation_validation.py:6`, `NessieAI/hibayes/judge_cache.py:7`,
  `NessieAI/hibayes/paid_run_state.py:7`, `NessieAI/hibayes/run_authorization.py:14`
  and `NessieAI/hibayes/spend_conservation.py:9`.
- See `nextseek_api/assistant/README.md` and `nextseek_api/assistant/CLAUDE.md` for what
  those tables are and who else writes them.
- Django's ORM and transaction machinery at module scope in four modules
  (`NessieAI/hibayes/generation_store.py:11-12`,
  `NessieAI/hibayes/paid_run_state.py:4-5`,
  `NessieAI/hibayes/run_authorization.py:10-12` and
  `NessieAI/hibayes/spend_conservation.py:7`), so importing any of them without
  configured settings raises.
- `NessieAI/router/family_labels.py` for the corpus taxonomy and hash, at
  module scope in `NessieAI/hibayes/human_grade_fit.py:24` and inside a function body at
  `NessieAI/hibayes/generation_validation.py:125`.
- `NessieAI/router/posterior_selector.py`, imported inside the replay driver at
  `NessieAI/hibayes/task6_replay.py:175`. This is the return leg of a cycle, described
  below.
- `NessieAI/tests/nessie_tests/bayes_manifest.py`, imported lazily by
  `NessieAI/hibayes/human_grade_fit.py`: one of the three frozen engine-to-harness edges.
- `dmac_assistant`'s generated BAML client, imported only after the provider flag is set
  (`NessieAI/hibayes/judge_human_compare.py:480-481`). The vendoring guard checks for
  `dmac_assistant.eval` and `tools.hibayes`, not for the router client
  (`NessieAI/tests/hibayes/test_eval_vendoring.py:27`), so this import is
  permitted by design.
- JAX, NumPyro and ArviZ, imported lazily inside the fit functions
  (`NessieAI/hibayes/fit/v14/quality_model.py:82-84`,
  `NessieAI/hibayes/fit/v14/latency_model.py:75-78`) and supplied only by
  `NessieAI/docker/eval/Dockerfile:21-23`.
- An authenticated delivery directory that is not in this repo, pinned by SHA-256 for
  three container files and six archive members
  (`NessieAI/hibayes/human_grade_fit.py:107-119`).

Read by path rather than imported, which is a different kind of edge:

- `NessieAI/router/router.py`, bound through `NessieAI/paths.py` as a module-scope
  constant at `NessieAI/hibayes/seam_inventory.py:24` and rebased at
  `NessieAI/hibayes/seam_inventory.py:128`, then parsed as AST. Nothing is imported from
  it, so a rename of that file breaks the scan without any import error.

Depended on by:

- Production code: exactly three modules, all in the router:
  `NessieAI/router/posterior_selector.py` (lazily, inside `get_active_snapshot`, so
  importing the router does not load this package),
  `NessieAI/router/risk_overlay.py:25` and
  `NessieAI/router/route_monitoring.py:9`. No other non-test module outside this
  directory imports this package.
- Mutual coupling, not a one-way edge. The selector reads this package's active
  generation, and the router calls the selector at
  `NessieAI/router/router.py:286`; the replay driver here imports that same
  selector back at `NessieAI/hibayes/task6_replay.py:175`, closing the loop.
- See `NessieAI/router/README.md` for the other end of that cycle and the rest
  of the routing surface.
- Tests: `NessieAI/tests/hibayes/`, including the vendoring guard at
  `NessieAI/tests/hibayes/test_eval_vendoring.py:18`, and the posterior-routing tests in
  `NessieAI/tests/router/`.
- The plan018 verification scripts that drove this package are archived in
  `NessieAI/history/plan018/scripts/` with the evidence they wrote.

Excluded from that list: this package's own internal cross-imports, and the retry-context
evaluator behind `/nextseek_api/evaluator/` (see the end of "HiBayes lives in these
places"), which appears in the `/nextseek_api/evaluator/` entries of `ci/routes.py`, in
`nextseek_api/assistant/descriptions_evaluator.py:43` and in
`NessieAI/tests/nessie_tests/FAMILIES.json:9647`, and touches nothing here.

See `NessieAI/hibayes/CLAUDE.md` for the invariants and the traps.
