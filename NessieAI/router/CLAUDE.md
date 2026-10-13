# Working in NessieAI/router/

## Invariants

- **BAML imports stay lazy and guarded.** `router.py` loads `NessieAI/dmac_assistant` only inside function bodies, and a failure becomes a fallback, never an exception. A dependency hiccup must degrade routing, never stop Django booting.
- **`<router_unavailable>` is a failure.** Treat it like a raised error and fall back to the heuristic. Treating it as a route sends every turn to CC.
- **Routing degrades; it never raises.** Every strategy's caller catches and falls through, so the only symptom of a broken input is a wrong `source` on the route decision. When you add an input, log at ERROR when it is missing.
- **The model id comes only from `NessieAI/dmac_assistant/build_context/router_model_class_map.json`.** Never hard-code one: the Bedrock proxy allows only the three ids a CC turn names (the map's `opus`, `opus_fallback` and `sonnet`), and a CC turn without an explicit id gets a 403.
- **Overlays observe; they never change the outcome.** `risk_overlay.py` and `route_monitoring.py` are telemetry.
- **Family labels come from the corpus**, `NessieAI/tests/nessie_tests/corpus.json`, reached through `NessieAI/paths.py`. The image must keep `NessieAI/tests/`.

## Landmines

- **`router.py` opens with a `try/except ImportError` dual import** so the module also loads outside the package. Adding a plain relative import at the top breaks the standalone path silently.
- **A test pins the source hash of `_heuristic`** (`NessieAI/tests/router/test_f_constraint_pins.py`). Never run a formatter over this package.
- **`DMAC_ROUTE_CAPABILITIES_FILE` and `DMAC_ROUTER_MODEL_CLASS_MAP_FILE` in a box's `docker/nextseek.env` beat the package defaults.** A stale value drops every turn to the heuristic, or strips the model id from every CC turn. See `NessieAI/CLAUDE.md` "Box env".
- **The override order in `policy.py` is load-bearing.** `_decide_route` applies an explicit force first, then calls `decide()`, and only then lets the `pipeline_agent` and sticky-CC guards redirect an NS-bound turn. A guard moved above the router captures turns the model never saw. `NessieAI/cc/turn.py` imports `_decide_route` and `_record_ledger_row` from here; nothing in this package imports `nextseek_api.services`.
- **`NessieAI/tests/router/test_route_capabilities.py` fails on other units' state.** It imports build_tools, dmac_assistant, the harness and the op registry at module scope, and one test runs `git show` on a pinned commit, so it fails wherever `.git` is not reachable. Keep that `git show` path as it is.
- **Posterior routing is off by default** (`NEXTSEEK_POSTERIOR_ROUTING_ENABLED`). A green run with the flag off says nothing about the posterior leg.

## Test command

See `NessieAI/tests/README.md`.

## See also

- `NessieAI/router/README.md`: strategies, overrides, inputs.
- `NessieAI/dmac_assistant/CLAUDE.md`: the BAML sources and the generated client.
- `NessieAI/hibayes/CLAUDE.md`: the posterior generations.
