# nessie_tests

Router-aware e2e harness for the Nessie assistant. Drives cases through the real
top-level router (`POST /nextseek_api/cc-assistant/query/async/`), reusing
`chat_nextseek/e2e`'s corpus + `PassCriterion` DSL, with **zero edits** to the
vendored `chat_nextseek`.

## Tiers
- **route** (fast, pre-merge): stops at the `route_decided` event; asserts
  `route`/`engine`/parser `mode`. No seed data or paid turn.
- **full** (paid, nightly): runs the turn to completion; asserts counts + bundle
  richness. Requires the **seeded v2 instance** (project ids 2-14).

## Scope
- `--scope specific` → only `route_gate`-tagged cases (+ consistency groups).
- `--scope all` → the full imported NS corpus + overlay.

## Run
Unit tests (host, isolated env): `cd nessie_tests && uv run pytest tests/ -v`
DB/contract tests (in-container): `docker cp` then
`docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek sh -c 'cd /app && uv run pytest nessie_tests/tests_container/ --no-migrations -v'`

Live route gate: `python -m nessie_tests --base-url http://localhost:8000 --tier route --scope specific`
Full pass: `python -m nessie_tests --base-url http://localhost:8000 --tier full --scope all`

## Cadence
Route gate = pre-merge. Full pass = nightly / pre-release on a seeded box.

**The route gate is cheaper, not free.** `--tier route` stops the *client*
polling once it sees `route_decided` (`http_driver.py:96-98`); it does not cancel
anything. The server started the turn on a daemon thread and returned 202, and
its only early return is the `unrelated` route
(`nextseek_api/services/cc_assistant.py:352-366`) — so every gate that routes
anywhere else runs to completion and bills for it after the harness has walked
away, on a CC gate a full Opus turn.

**No route is free, `unrelated` included** — it is only the cheapest. The BAML
router call (`_decide_route` → `cc_router.decide` → `_baml_decision`) is made on
every turn, and `route_decided` is emitted at `cc_assistant.py:347-350` *before*
the `ROUTE_UNRELATED` check at `:352`. `unrelated` skips the answering turn, not
the router that decided to skip it.

Cost is read off `query_complete`, which route-tier polling never reaches, so a
route run cannot account for what it spent. It reports `unmeasured`, not `$0`.
A full-tier total is a floor too, for two independent reasons: NS-routed turns
are never priced at all (`chat_nextseek` logs a per-call token ledger to a *log
file*, with no USD conversion anywhere and nothing attached to `query_complete`),
and a CC turn that ends in `query_error` carries no cost field either. Any run
mixing the two prints `PARTIAL`.

## Known-fail (RED) cases
`nessie_repro` family + the `#33` consistency group are tagged `known_fail`:
they encode #32/#33 + the EOF/cypher bugs and are EXPECTED to fail until fixed.
`gate_failed()` excludes them, so they don't break the gate. Remove the
`known_fail` tag when the corresponding fix lands (that flips them into real
regressions).

## Cases that asserted nothing (`no_assertions`)

Some criteria cannot be observed over HTTP and are recorded `skipped` rather than
failed — `pipeline_agent.*`, `chat_log.*`, `ui_text.*`, `op: trio_match`, and (only
on a `container_cc` turn) the four derived NS outcome fields
`api_outcome_observed` / `graph_outcome_observed` / `report_produced_output` /
`outcome_observed`. Those four read keys off `query_complete.debug`, and a CC
`query_complete` carries no `debug` at all, so they are constant-false on a CC
turn no matter what it did. The skip is conditional on the observed route: on an
NS turn the same four fields are real assertions and still fail.

**Scale, stated honestly.** Skipping them removes ONE of the reasons a CC-routed
case in a floored family goes red — not all of them. Simulate every case in the
resolved corpus routing CC and **267 of 280 are still red**, with all six floored
families at 100%: the inline `route` (227 variants), `parser_plan.mode` (216),
`api_ok` (136) and `api_plan.endpoint` (116) criteria are deliberately not
skipped, so those cases stay red until the corpus itself is settled. The one
variant this measurably turns green is `tree.then_ask_about`, the only multi-turn
variant in any floored family and therefore the whole realistic mixed-route
population today.

The skip does **not** extend to `api_ok`, `neo4j_ok`, `parser_plan.*`,
`api_plan.*` or `graph_result.*`. Those are inline, hand-written case criteria —
a case carrying them is claiming a particular engine answered it — so a CC-routed
case with an inline `api_ok` still fails. That is deliberate; changing it is a
corpus decision, not a harness one.

The guard against the obvious hazard: a case that evaluated **zero** criteria
(every one skipped, or none carried) is recorded `status="no_assertions"`, never
`passed`, and it counts as a **real failure** — a case proving nothing is corpus
drift, exactly like an `xpass`. `known_fail` does not excuse it: the tag claims
the case fails, and a case that tested nothing showed neither that nor its
absence. The rule is per CASE, not per turn — a multi-turn case that really
asserted something on one turn did assert something, and the vacuous turn stays
visible in the report's observation table with every row marked SKIPPED.

## Provider outages (GREY)
A reply carrying `nessie_tests.outage.PROVIDER_OUTAGE_MARKER` means every
provider in an agent's fallback chain returned 503, so the turn never reached
the product. Those cases are recorded `status="error"` with `outage=True`,
reported on their own line, and excluded from the gate — an outage is not a
regression. Every *other* `error` (a dead endpoint, a timeout) still fails the
gate. Ten of the eighteen reds in the 2026-08-03 seed-6 run were one Bedrock
outage; nine were visible in the manifest and the tenth was hidden inside the
`#33` consistency group, which reports its own summary instead of its members'
replies. `run_group` checks for an outage before composing that summary.
