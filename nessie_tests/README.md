# nessie_tests

Router-aware e2e harness for the Nessie assistant. Drives cases through the real
top-level router (`POST /nextseek_api/cc-assistant/query/async/`), reusing
`chat_nextseek/e2e`'s `PassCriterion` DSL, with **zero edits** to the vendored
`chat_nextseek`.

The corpus is **forked, not reused**: nessie reads its own
`nessie_tests/corpus.json`, adopted from `chat_nextseek/e2e/catalog.json` on
2026-08-04 and hand-curated since. `catalog.json` is unchanged and still serves
its own ten readers; `tests/test_catalog_drift.py` fails when it moves, so the
divergence is never silent, and adopting an upstream change stays a deliberate
edit.

## Tiers
- **route** (fast, pre-merge): stops at the `route_decided` event; asserts
  `route`/`engine`/parser `mode`. No seed data or paid turn.
- **full** (paid, nightly): runs the turn to completion; asserts counts + bundle
  richness. Requires the **seeded v2 instance** (project ids 2-14).

## Running only the NOT-YET-GRADED variants (delta run)

`--bayesian` drives every variant flagged `is_bayesian` + `active` — 152 today,
of which 127 are already graded from the 2026-08-06 run. Running all 152 REPAYS
about $30 to re-answer questions a human has already judged.

To run only the 25 that have never been graded:

```bash
python nessie_tests/scripts/delta_selection.py --graded nessie_bayes_full/grades.json
# ... run --bayesian into its OWN --out ...
git checkout nessie_tests/corpus.json   # the narrowing is run-time state, not a commit
```

Full procedure, including grading and merging the two runs into one HiBayes-ready
study: `docs/nessie-corpus-additive-2026-08-06.md`.

## Scope
- `--scope specific` → only `route_gate`-tagged cases (+ consistency groups).
- `--scope all` → the whole active corpus (`nessie_tests/corpus.json`).

## Run

### Unit tests — host lane (fast, no container)

From the **repo root**, this exact invocation:

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider
```

**Baseline: everything passes.** Deliberately not a number. This file said
"403 passed" for three commits and was wrong by the fourth — inside its own
branch — which is the same rot the rest of this document exists to correct. Take
the count from the run, not from here:

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider 2>&1 | tail -1
```

The figure that IS fixed, and is pinned by a test rather than by prose, is the
size of the resolved corpus: `corpus.merged(Path('nessie_tests/corpus.json'))`
returns **308** variants
(`tests/test_floor_ops.py::test_the_two_overrides_replace_in_place_and_do_not_grow_the_corpus`).

`--no-project` and the explicit `--with` list are load-bearing, not decoration.
Both ways of getting them wrong fail in a way that does not name the cause:

- **Plain `uv run pytest` never reaches a test**, and neither does
  `cd nessie_tests && uv run pytest tests/` — `nessie_tests/` has no
  `pyproject.toml`, so uv resolves the repo-root project, which depends on
  `mysqlclient`, which will not build on the host
  (`Exception: Can not find valid pkg-config name`).
- **Dropping `--with beautifulsoup4` does not present as a missing dependency.**
  `chat_nextseek/e2e/playwright/trio.py:11` imports `bs4`, and `run_suite` catches
  every `Exception` as infrastructure (`runner.py:268`), so a case that hit the
  import is recorded `status="error"`. Most of the failures that follow do name
  `ModuleNotFoundError: No module named 'bs4'`, but a large minority surface as
  `AssertionError: assert 'error' == 'passed'` (or `'failed'`, `'xpass'`,
  `'no_assertions'`) and never mention bs4 at all. **The reliable tell is the
  string `bs4` appearing anywhere in the output.** No counts here on purpose:
  this bullet has pinned a pair twice and both went stale — "63 failures, 43
  naming bs4" was measured at `a85dde9` and was 431 failed / 293 passed three
  commits later. The string does not move. Do not chase the silent ones as real
  regressions.

### DB/contract tests — in-container lane

`nessie_tests/tests_container/` needs Django settings and a live database, so it
cannot run on the host lane. `docker cp` the tree in, then:

```bash
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nessie_tests/tests_container/ --no-migrations -v'
```

### Live runs

Route gate: `python -m nessie_tests --base-url http://localhost:8000 --tier route --scope specific`
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

**No variant in the resolved corpus is tagged `known_fail` any more.** None of
the 308 variants returned by `corpus.merged(Path('nessie_tests/corpus.json'))`
carries that tag — which is a statement about `known_fail` only; they carry
plenty of other tags, and 288 of the 308 carry an injected `route` criterion (see
"Scale" below). Four DEFINITIONS still carry `known_fail` in `corpus.json` —
`repro.parent_attr_aggregate` (#32a), `repro.thin_bundle_recall` (#32b),
`repro.eof_truncation_reporter` (the reporter EOF bonus) and
`advanced.find_me_nhp_samples_from_study_ns_graph` — but all four were retired on
2026-07-30 in the issue-#35 review, and `merged()` returns active definitions
only. `corpus.load_all_definitions()` will tell you there are four; in a run
there are none. (The whole `nessie_repro` family is retired, including
`repro.cypher_uid_dot`, which was already re-tagged `fixed` before that.)

The one `known_fail` entity a run still produces is the consistency group
`cons.nhp_sequencing_engine` (#33 — the same NHP-sequencing question asked two
ways must agree on route and count). It survives because **retirement applies to
variants only**: groups are loaded separately by `load_consistency_groups()`,
which reads the `consistency_groups` block of `corpus.json` and never looks at
any variant's `status`.

`_is_real_failure` excludes an expected failure (`runner.py:415-416`), so
`gate_failed()` does not count it and the group does not break the gate. Two
things the tag does NOT excuse, both deliberate: a `known_fail` that PASSES is
recorded `xpass`, and one that evaluated zero criteria is recorded
`no_assertions`. Both count as real failures — the tag is a claim that the case
fails, and neither outcome demonstrated that. Remove the tag when the fix lands
(which flips the case into a real regression guard).

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
resolved corpus routing CC and **295 of 308 are still red**, with all six floored
families at 100%. Four criteria account for nearly all of it, and none of them is
skipped: `route` fails on **231** variants, `parser_plan.mode` on **217**,
`api_ok` on **130** and `api_plan.endpoint` on **106**. Those cases stay red
until the corpus itself is settled.

**Name the frame, because the two frames disagree.** Under that all-CC
simulation the skip turns *nothing* green: the green set is 13 variants with the
skip and the *same* 13 with it monkeypatched off. `tree.then_ask_about` is red
there too — its SEED turn asserts `api_ok` and `api_plan.endpoint` inline, and an
all-CC run fails both before the follow-up is ever reached.

The measurable payoff is the **mixed-route** case, which is what a real run
actually produces: an NS seed followed by a CC follow-up. `tree.then_ask_about`
is the only multi-turn variant in any floored family, so it is that entire
population, and in that frame it goes red -> green. Pinned by
`tests/test_evaluate.py::test_the_one_mixed_route_variant_in_a_floored_family_now_passes`,
which drives the seed NS and the follow-up CC and asserts the follow-up's
`outcome_observed` is SKIPPED rather than satisfied — `augment_debug` still
resolves it `False`, so the case is green because the criterion is no longer
scored, not because it started holding.

**Every figure above is RECOMPUTED, not remembered**, in
`tests/test_write_refusal_coverage.py`: the headline by
`test_the_cc_routing_simulation_quoted_in_the_docs_is_reproducible` (308 total /
13 green / 295 red), the four per-criterion counts by
`test_the_four_criteria_the_docs_blame_for_the_red_are_recomputed_too`, and the
two-frames claim by `test_the_cc_skip_turns_nothing_green_under_the_all_cc_simulation`.
All three drive the resolved corpus through `evaluate.evaluate_turn` with the real
CC payload shape (a reply and no `debug` key), and all three fail naming this
paragraph and the matching comment in `tests/test_evaluate.py` as the places to
update. That second test exists because the headline WAS recomputed while the four
counts beside it were not, and they had drifted to 227/216/136/116 — a fresh
number vouching for stale ones is worse than neither. The headline read
`267 of 280` until the write/delete refusal cases were restored on 2026-08-03; the
green set did not move, because all three of those cases are red under the
simulation.

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
visible in `report.html`'s observation table: each of its rows is classed
`skipped`, labelled `SKIPPED` in words, carries the reason that skipped it, and
is counted apart from the passes in the table's summary line, which reads
`observed (6 criteria, 2 skipped)` and never `all passed`.

That sentence was FALSE for the whole of its first day in this file: `report.py`
never read `CriterionObservation.skipped`, a skipped row carries `passed=True`,
so a fully vacuous turn rendered as green rows inside an "all passed" count. It
is now held by
`tests/test_manifest_report.py::test_the_vacuous_turn_the_docs_promise_really_is_visible`,
which drives the real `tree.then_ask_about` through the real runner and greps
the generated HTML.

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
