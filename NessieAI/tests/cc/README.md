# `NessieAI/tests/cc/`

## What this is

The tests for the Container-CC engine (`NessieAI/cc/`), its op registry, the plugin shims and the
three AI images, plus the tooling for the Step 7 acceptance gate. Most files are plain tests; a few
are gate tooling and are not collected as tests.

## Layout

| Path | What it holds |
|---|---|
| `test_*.py` | the tests: engine environment, mounts, timeouts and turn loop, provisioning, memory and summaries, transcripts and scrubbing, the proxy allow list and failover, the sidecar, op registry export, and the `test_step7_*_port.py` guards that pin each image's `PORT-EVIDENCE.json` |
| `bin_inventory.py`, `image_context.py` | helpers that read the plugin `bin/` inventory and the image context from disk |
| `validate_cc_acceptance.py`, `validate_step7_compose_deploy.py` | validators for acceptance bundles and the compose deploy; the "Bundle re-check" block in `NessieAI/tests/README.md` runs them |
| `step7_gate_catalog.py`, `step7_per_op_evidence.py`, `step7_preflight_collector.py`, `step7_compose_fixtures.py`, `cc_matrix_gate_harness.py` | the Step 7 live-gate harness |
| `step7_catalog/` | the committed exercise catalog and instance binding; `R26-live-gate-prereqs-runbook.md` lists what the live gate needs |
| `scripts/` | gate drivers run by hand (`step7_gate3d_*.py`, `full_ui_e2e.py`, `verify_*.py`) |
| `acceptance_evidence/step7/` | generated run bundles; see its `README.md` |
| `fixtures/` | recorded CC transcripts |

## Running

Free lanes: the CC clean lane (inside the `nextseek` container) and the CC hermetic lane (host uv).
`test_cc_realstack.py` runs only with `RUN_REALSTACK=1` and spends real LLM budget, so it needs the
owner's approval per run. The `host_only` tests check the source tree and have their own lane. Every
command is in `NessieAI/tests/README.md`; do not copy it elsewhere.

## Depends on / depended on by

Tests `NessieAI/cc/` (`NessieAI/cc/README.md`), `NessieAI/docker/` (`NessieAI/docker/README.md`) and
`NessieAI/cc/op_registry/`. The skill `/add-cc-op` ends by running these.
