# NessieAI/router/

Decides which engine answers a chat turn. Split out of `nextseek_api/cc_assistant/`; the
Django shell of that app (`apps.py`, `cc_sweep.py`, `cc_upload_tasks.py`, `cc_endpoint_guards.py`)
stays in `nextseek_api/`.

## Surface

| Module | What it does |
|---|---|
| `router.py` | `decide()`, the one public entry point, and the three route constants `nextseek_query`, `container_cc`, `unrelated` |
| `posterior_selector.py` | the comparative-posterior selector, on only when `NEXTSEEK_POSTERIOR_ROUTING_ENABLED` is set; reads HiBayes generations |
| `family_labels.py` | the classifier label space, read from `NessieAI/tests/nessie_tests/corpus.json` through `NessieAI/paths.py` |
| `router_context.py`, `baml_introspect.py`, `transport_trace.py` | the context fed to BAML and the traces of each call |
| `risk_overlay.py`, `route_monitoring.py` | telemetry overlays: they observe the outcome and never change it |
| `turn_ledger.py` | writes one routing row per turn, failed turns included, under the chat's next free turn number and linked to the turn's `QueryTask`, through `nextseek_api.assistant.models_db` |
| `policy.py` | `_decide_route`, the override precedence around `decide()` (see Overrides), and `_record_ledger_row`, which writes the turn's row through `turn_ledger.py` |

## How a route is chosen

`decide()` tries three strategies in order, and the first that answers wins:

1. **Posterior selector**, only when `NEXTSEEK_POSTERIOR_ROUTING_ENABLED` is on (off by default). A returned selection skips BAML entirely.
2. **BAML router**: `RouteQuery` from `NessieAI/dmac_assistant/`, fed by a classifier that assigns a task family, not a route.
3. **Keyword heuristic**, when BAML is unreachable, raises, or returns `<router_unavailable>`.

Routing degrades; it never raises. A missing corpus, a missing build context or a BAML failure
drops a turn to the heuristic and logs, so a wrong route is the only symptom. The route decision
in the Debug panel shows `source` (`baml`, `heuristic`, `posterior`, `forced`, `sticky`).

## Overrides

Applied by `_decide_route` in `policy.py`, in this order:

`force_route` > `pipeline_agent` > sticky CC > the router.

- `force_route` (`ns` or `cc`) and the `cc/query/async/` endpoint beat the router, for admins only. A non-admin `force_route` is ignored.
- An open `pipeline_agent` wizard only keeps a turn the router already sent to NExtSEEK.
- **Sticky CC**: when the previous turn in the chat routed `container_cc` and completed, an NS-classified turn becomes `container_cc` (`source: "sticky"`). `unrelated` is never converted, and a CC turn that errored does not make the chat sticky.
- The chat stays on CC until a new chat, an admin `force_route`, or an intervening `unrelated` turn. That last exit is an accepted consequence of the rule, not a bug.

## Inputs this package reads

| Input | Where |
|---|---|
| BAML prompts | `NessieAI/dmac_assistant/baml_src/` (`router.baml`, `classifier.baml`) |
| Route capabilities (generated) | `NessieAI/dmac_assistant/build_context/route_capabilities.json`; its standing ruling is `NessieAI/docs/dev-v5-merge-decisions.md` |
| Model map (hand-kept) | `NessieAI/dmac_assistant/build_context/router_model_class_map.json` |
| Family labels | `NessieAI/tests/nessie_tests/corpus.json` |
| Posterior generations | `NessieAI/hibayes/` generation store |

## Running and testing

Tests are in `NessieAI/tests/router/`; commands are in `NessieAI/tests/README.md`.

## Depends on / depended on by

- Depends on `NessieAI/dmac_assistant/` (lazily), `NessieAI/hibayes/` (posterior leg, lazily: importing the router does not load it), `nextseek_api.assistant.models_db` (ledger), and `chat_nextseek`'s pipeline agent (`policy.py` asks it whether a wizard is open).
- `NessieAI/hibayes/` imports `family_labels` back. That loop has no load-time cycle.
- Called by `NessieAI/cc/turn.py`, the CC turn behind `nextseek_api/services/cc_assistant.py`.
