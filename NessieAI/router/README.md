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

1. **Posterior selector**, only when `NEXTSEEK_POSTERIOR_ROUTING_ENABLED` is on (off by default). A returned selection skips BAML entirely. Its family call, `ClassifyQuery`, runs under `ROUTER_PRIMARY_LIMIT_S` too, and a timeout is handled as an error: the BAML router below decides.
2. **BAML router**: `RouteQuery` from `NessieAI/dmac_assistant/`, fed by a classifier that assigns a task family, not a route. It runs on the client the function declares (`GCPReasoner`) under `ROUTER_PRIMARY_LIMIT_S`, BAML's retries included; on a timeout, an error or `<router_unavailable>` it gets one try on `GCPFlash` under `ROUTER_FALLBACK_LIMIT_S`, through a per-call `ClientRegistry` (no `.baml` edit).
3. **Keyword heuristic**, when BAML is unreachable, or both of those calls fail.

The decision records `router_model` (the model that answered; none for the heuristic or a forced
turn) and `router_fallback` (`from`, `to`, `reason`), and the CC turn puts both on `route_decided`.
`decide()` also attaches a `baml_py.Collector` to each BAML call it makes and prices every attempt
in it, BAML's retries included, with `NessieAI/chat_nextseek/model_prices.json`: `router_cost_usd`,
`router_usage` (per call: client, model, status, tokens, thinking read from the response body)
and `router_cost_partial` (a call cut off by the time limit, a call nothing was logged for, an
unpriced model or unreadable thinking). The policy carries all five fields through every decision
it rebuilds (`router_record`), and `route_decided` gets the three cost fields on every routed turn,
`unrelated` included; a forced turn made no router call and gets none (`router_cost_fields`).

Routing degrades; it never raises. A missing corpus, a missing build context or a BAML failure
drops a turn to the heuristic and logs, so a wrong route is the only symptom. The route decision
in the Debug panel shows `source` (`baml`, `heuristic`, `posterior`, `forced`, `pipeline`, `sticky`,
`followup`, `cc_unavailable`).

## Overrides

Applied by `_decide_route` in `policy.py`, in this order:

`force_route` > `pipeline_agent` > a turn that refers back goes to CC > the router.

- `force_route` (`ns` or `cc`) and the `cc/query/async/` endpoint beat the router, for admins only. A non-admin `force_route` is ignored.
- An open `pipeline_agent` wizard only keeps a turn the router already sent to NExtSEEK.
- **Follow-ups and sticky CC** (2026-09-24 split; `NESSIE_FOLLOWUP_ROUTING`, default `split`): a follow-up is a turn that refers back to an answered turn of the chat (a back-reference cue in `followup.py`: "of those", "which of them", "that chart", "the file", "remind me", "what query did you run"). Under `split`, one NExtSEEK can answer from the earlier result or by re-running the earlier search (count, filter, a breakdown by one or two fields, recall, a one-change re-run) stays where the router sent it; one that names a file, a download, a chart, code, or a comparison, summary or analysis (`followup_shape`) becomes `container_cc` (`followup`); and in a chat with a completed CC turn any follow-up becomes `container_cc` (`sticky`). `cc` restores the 2026-09-23 rule (every follow-up to CC) with no rebuild. The router prompt renders the same rule from `followup.followup_rule_text()` (`RouterInput.followup_rule`); this guard is its deterministic backstop and never moves a turn toward NExtSEEK. A self-contained question (no back-reference) is left to the router. `unrelated` is never converted. The whole `chat_log` is scanned, not the 5-turn window.
- **CC unavailable**: a turn this policy moved to CC (`sticky`, `followup`) runs on NExtSEEK for that one turn when the CC runner is down (`source: "cc_unavailable"`, `_fallback_when_cc_unavailable`). A turn the router or an admin sent to CC keeps its error.
- An admin's `force_route` and an open `pipeline_agent` wizard override the rule for one turn.

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
