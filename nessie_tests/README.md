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
Route gate = pre-merge (cheap). Full pass = nightly / pre-release on a seeded box.

## Known-fail (RED) cases
`nessie_repro` family + the `#33` consistency group are tagged `known_fail`:
they encode #32/#33 + the EOF/cypher bugs and are EXPECTED to fail until fixed.
`gate_failed()` excludes them, so they don't break the gate. Remove the
`known_fail` tag when the corresponding fix lands (that flips them into real
regressions).

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
