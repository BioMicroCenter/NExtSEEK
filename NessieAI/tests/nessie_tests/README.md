# `NessieAI/tests/nessie_tests/`

## What this is

The router-aware end-to-end harness for the NExtSEEK assistant. It asks the
product questions the way a browser does (one POST to the async query endpoint,
then a poll loop against that task's progress endpoint:
`NessieAI/tests/nessie_tests/http_driver.py:7`, `NessieAI/tests/nessie_tests/http_driver.py:38-54`) instead
of importing either engine and calling it directly. That choice is the whole
design: the top-level router is what decides which engine answers, and a
harness that imported an engine would never exercise the decision.

Two things about the directory are easy to misread from outside it.

**It is not only a test tree.** Code that ships imports it: the paired-evidence
ingester in the Container-CC op registry, the `manage.py nessie` command, HiBayes
human-grade fitting and the generator behind `route_capabilities.json`, and the live
router reads its `corpus.json` for family labels. Three of those imports are frozen
engine-to-harness edges (`NessieAI/CLAUDE.md` "Boundary"). Deleting or renaming one of
those modules is an application change, not a test change. The importers are listed
under "Depends on / depended on by" below.

**What a run observes is narrower than what a run causes.** At `--tier route`
the client stops polling the moment it sees the routing event
(`NessieAI/tests/nessie_tests/http_driver.py:137-139`), and stopping the poll is all it does:
searching every non-test module of this package for a DELETE method, a cancel or
abort call, or a revoke returns nothing, so the server is never told to stop. It
has already returned 202 and is running the turn on its own thread, and its
single early return is the `unrelated` route
(`NessieAI/cc/turn.py:350-363`), so every gate routed anywhere
else runs to completion and bills for it after the harness has walked away. That
is why a route-tier run reports its spend as partial (the router's price only) or
unmeasured, never as a total or a zero
(`NessieAI/tests/nessie_tests/manifest.py:233-242`). No route is free: the router's own model
call happens on every turn and the routing event is emitted before the
`unrelated` check, not after (`NessieAI/cc/turn.py:344-350`).

It reuses the `PassCriterion` DSL of the catalog-driven E2E runner in
`NessieAI/tests/e2e/` with **zero edits** to it, and imports it by package
(`NessieAI/tests/nessie_tests/corpus.py:7-8`, `NessieAI/tests/nessie_tests/evaluate.py:7`).
There is no `sys.path` insertion, and `NessieAI/tests/api/test_nessie_boundaries.py`
fails on any bare `e2e` or `pathsetup` import.

The question set is forked from the E2E catalog, not shared with it. The
harness reads its own `corpus.json`, adopted from `NessieAI/tests/e2e/catalog.json`
on 2026-08-04 and hand-curated since; the upstream catalog is unchanged and
still serves its own readers, and a drift test fails if that file moves
(`NessieAI/tests/nessie_tests/corpus.py:11`, `NessieAI/tests/nessie_tests/tests/test_catalog_drift.py:1`), so
the divergence is never silent and adopting an upstream change stays a
deliberate edit.

## Tiers
- **route** (fast, pre-merge): stops the *client* at the `route_decided` event;
  asserts the routing decision. No seed data. Not free and not side-effect-free;
  see "Cadence".
- **full** (paid, nightly): runs the turn to completion; asserts counts + bundle
  richness. Requires an instance **seeded with the dataset the corpus's ground
  truth was verified against**, and it runs only inside the application
  container; see "Two entry points" below, **before** spending money.

### What the route tier can and cannot observe

`RouteObservation` (`route_observer.py:10-17`) carries `route`, `model_class`,
`source`, `reasoning`, `parser_mode`, `engine`. At route tier the poll loop
breaks the first time `route_decided` appears (`http_driver.py:137-138`), and
everything the observation needs beyond that one event arrives LATER:
`parser_mode` comes from the parser's `agent_complete` event or from
`query_complete.debug`, and the NS branch of `route_observer._engine`
(`route_observer.py:53`) derives graph-vs-REST from exactly those. So:

- `route`, `source`, `model_class`, `reasoning`: reliably observed at route
  tier: all four ride on `route_decided` itself.
- `engine` on a `container_cc` or `unrelated` turn: also reliable
  (`container_cc:<model_class>` / `unrelated`, derived from `route_decided`
  data alone).
- `engine` and `parser_mode` on a `nextseek_query` turn: NOT reliably observed
  at route tier. They are `None` unless a poll happened to land after the
  parser had already run, which is a race, not a contract.

Stated plainly: **the graph-vs-REST split** (the `graph_query` /
`reporter` / `/nextseek_api/samples/advanced_search/` values the full-tier
report shows in its engine column) **is a full-tier observation.** Verifying a
routing change at the ns/cc/unrelated level is route-tier work; verifying WHICH
NS engine answered needs the paid full tier via `manage.py nessie`. And note
that today's three `route_gate` variants assert `route` only, so the route gate
as it stands exercises the top-level split and nothing deeper.

## Surface

The surface has three different shapes, so it is described three ways.

**As an importable package**: the entry points other code actually calls:

| Module | What it is for |
|---|---|
| `NessieAI/tests/nessie_tests/cli.py:88-137` | flag parsing; `NessieAI/tests/nessie_tests/cli.py:10-26` documents nine exit codes |
| `NessieAI/tests/nessie_tests/runner.py:409-413` | `run_suite`, one whole run; `NessieAI/tests/nessie_tests/runner.py:142-146` is one case |
| `NessieAI/tests/nessie_tests/corpus.py:415` | `merged`, the resolved active corpus |
| `NessieAI/tests/nessie_tests/evaluate.py:662` | `evaluate_turn`, criterion scoring for one turn |
| `NessieAI/tests/nessie_tests/manifest.py:233` | `cost_summary`, what a run may claim about money |
| `NessieAI/tests/nessie_tests/turn_cost.py:112-151` | how one turn's and one case's spend are summed; standard library only, so the output skill's pull loads it by path |
| `NessieAI/tests/nessie_tests/bayesian.py:76` | `run_paired`, the paid dual-route run |
| `NessieAI/tests/nessie_tests/preflight.py:94-98` | refuses a paid run whose force did not land |
| `NessieAI/tests/nessie_tests/export.py:961` | paired manifest to the locked HiBayes CSVs |
| `NessieAI/tests/nessie_tests/collect.py:377` | post-hoc artifact collection for a paired run |
| `NessieAI/tests/nessie_tests/sources.py:407` | the container-backed reads `collect` needs |
| `NessieAI/tests/nessie_tests/v4_2_verifier.py:330` | replay verifier over a delivered result set |
| `NessieAI/tests/nessie_tests/bundle.py:46-107` | full-tier bundle richness; its Django import is lazy, and its `preflight` runs before the first full-tier turn |

Invoked as `python -m NessieAI.tests.nessie_tests` through `NessieAI/tests/nessie_tests/__main__.py:2`, or
in-container as a Django management command
(`nextseek_api/management/commands/nessie.py:42-43`).

**As committed data.** `NessieAI/tests/nessie_tests/corpus.json` is the only corpus source
there is, and `NessieAI/tests/nessie_tests/corpus.py:415-425` records that the superseded
overlay files and their generator were deleted outright. `FAMILIES.json` declares the
32 task families the corpus is mapped onto (28 code-derived, and 4 added by hand on 2026-09-23
for the production researcher questions)
(`NessieAI/tests/nessie_tests/FAMILIES.json:4-5`, `NessieAI/tests/nessie_tests/scripts/remap_families.py:2`). `NessieAI/tests/nessie_tests/probes/` holds three
hand-authored case files replayed by `NessieAI/tests/nessie_tests/tests/test_probe_files.py:1`.
Measured 2026-09-24 against `NessieAI/tests/nessie_tests/corpus.json`: `merged` resolves 474
variants over 538 turns; `curated`, which drops the unreviewed atlas set
(`NessieAI/tests/nessie_tests/corpus.py:110-130`), leaves 416; `bayesian_ids` selects 153; one
consistency group is defined; and 3 variants carry the `route_gate` tag.

**As two packaged skills.** Each carries its own SKILL.md and is not restated
here. See `NessieAI/tests/nessie_tests/output-skill/SKILL.md:2-3` for turning a finished run
into a triage report, and `NessieAI/tests/nessie_tests/output-skill-bayesian/SKILL.md:2-3` for
the paired run's blind-grading report. A hyphen is not a Python identifier, so
the testable logic for the second one lives in the underscore-named package
beside it and the skill's script is a thin entry point
(`NessieAI/tests/nessie_tests/output_skill_bayesian/__init__.py:1-11`).

**As one-off tooling.** `NessieAI/tests/nessie_tests/scripts/` is a group of corpus-maintenance
programs, not a public API: they author and apply question sets
(`NessieAI/tests/nessie_tests/scripts/build_qset.py:1`, `NessieAI/tests/nessie_tests/scripts/apply_qset.py:1`,
`NessieAI/tests/nessie_tests/scripts/qset_data.py:1`), generate variants from the capability
atlas (`NessieAI/tests/nessie_tests/scripts/atlas_variants.py:2`), remap families
(`NessieAI/tests/nessie_tests/scripts/remap_families.py:2`), narrow a paired selection to
what is not yet graded (`NessieAI/tests/nessie_tests/scripts/delta_selection.py:1`), and render
the reviewable question-set document (`NessieAI/tests/nessie_tests/scripts/build_doc.py:1`).
They read and write `NessieAI/tests/nessie_tests/corpus.json` in place, so a narrowing is
run-time state to be reverted, never committed. `NessieAI/tests/nessie_tests/scripts/nessie`
is a thin shell entry point and names no path.

## Two entry points, and the full tier needs the app's Django

`python -m NessieAI.tests.nessie_tests` (the module CLI, `cli.py`) and `manage.py nessie`
(`nextseek_api/management/commands/nessie.py`, run inside the application
container) drive the same runner. At `--tier full` both wire the same
bundle-richness reader, `bundle.summary_for_session`, which reads `ChatSession`
rows through Django. So **the full tier runs only where the app's Django and
database are: inside the application container**, from either entry point.

**It used to fail only after spending the money (plan task 8.4, fixed
2026-09-17).** Nothing on the module CLI's path configured Django, and the
runner drives each paid turn (`runner.py:267`) BEFORE it reads the bundle
(`runner.py:309`), inside a catch that records any exception as infrastructure
(`runner.py:363`). So every full-depth case billed its turn, evaluated zero
criteria and recorded `status="error"`: `No module named 'django'` on the host,
`AppRegistryNotReady` inside the container. Both shapes were hit in the week of
2026-08-17, and both runs billed in full. The tell was every full-depth case
carrying the same one-line reason naming django, while the `route_gate` cases
and the consistency groups looked normal.

Two changes close it, and a third closes a related shape found after it:

- **The reader configures Django itself when nothing has.** `bundle.ensure_django`
  (`bundle.py:46-72`) sets `DJANGO_SETTINGS_MODULE` to `dmac.settings` if it is
  unset and calls `django.setup()`; inside `manage.py nessie` it does nothing.
  So the module CLI's full tier now works inside the container too.
- **A full-depth run proves its reader before the first turn.** The reader
  carries a `preflight()` (`bundle.py:86-107`) that configures Django and reads
  the real table for a session that cannot exist. `runner.check_bundle_reader`
  (`runner.py:728`) calls it at the top of `run_suite` at `--tier full`
  (`runner.py:430`) and in `run_arms` before the arms preflight, which bills its
  probe turns (`runner.py:914`). A reader that cannot read raises
  `BundleReaderUnavailable` with nothing sent: the module CLI exits 9
  (`cli.py:32`), `manage.py nessie` raises a `CommandError`, and both say
  nothing was billed. On the host that is the expected outcome of `--tier full`.
- **A full-depth run proves its reader reads the instance the turns run on.**
  `preflight()` proves only that SOME database is readable: a reader configured
  for instance B passes it while the turns run on instance A, and then every
  turn bills on A and every bundle read on B finds nothing. So
  `runner.check_bundle_reader` next opens an empty chat on `--base-url`
  (`http_driver.make_session_clients`: a plain session create, no turn and no
  model, so free), asks the reader whether its database holds it (the reader's
  `holds_session` hook, `bundle.session_exists`), and deletes it. A reader that
  does not hold it raises `BundleReaderOtherInstance`, a `BundleReaderUnavailable`,
  so the same exit 9 or `CommandError` follows with nothing sent; so does a probe
  chat that cannot be opened. A probe chat that cannot be deleted only warns.

`manage.py nessie` is still the entry point to use for paid runs. It is the one
verified to produce real per-criterion expected/observed grading, and it also
exposes `--cases` (run an explicit hand-authored case list), which the module CLI
deliberately does not.

**The route tier is fine from either entry point.** Below `--tier full` the
CLI leaves `bundle_reader` as `None`, and nothing else on the route path
imports Django: `runner.py` imports corpus/evaluate/http_driver/report/
manifest, all host-safe under the unit lane's `--with` list.

## Where to run this

- **Full tier: inside (or with the environment of) the application container**,
  via `manage.py nessie`. It needs Django, the app settings and the database
  all reachable, which is why it cannot run from a developer laptop: tried
  there, it refuses before the first turn (see "Two entry points" above).
- **Route tier: anywhere that can reach the base URL**, via the module CLI.
- `--base-url` names the instance under test. From inside the application
  container that is the app's own bind address (the management command defaults
  to `http://localhost:8000` for exactly this case); from outside, it is
  whatever URL fronts the instance.
- **The bundle reader reads `ChatSession` rows from the database its own
  environment is configured for** (`bundle.py:75-83`), not from anything behind
  `--base-url`. So driving instance A's endpoint from instance B's environment
  cannot work: the session ids A's turns create exist only in A's database, and
  every bundle read misses. Environment and endpoint must belong to the SAME
  instance, and a full-depth run checks that before its first turn (the probe
  chat in "Two entry points" above): a mismatched pair is refused, nothing billed.
- `--out` should be a path that survives the container (a mounted or
  bind-mounted directory), or the run directory must be copied out afterwards
  (`docker cp <app-container>:<out-dir> .`). A path on the container's own
  ephemeral filesystem is lost when the container is replaced, and a paid run's
  `manifest.json`, `report.html` and grades are the only record that the money
  bought anything.

## The selection

`--bayesian` drives every variant flagged `is_bayesian` + `active`: **153 today**,
the 149 of the 2026-08-06 question set plus one case from each of the four families added
2026-09-23 (`pub.pmid_bare`, `person.lau_lab_samples`, `file.flow_file_for_a_patient`,
`export.session_to_the_developers`), which that document does not list. 25 of the 26 task families, one distinct question per
variant, and every one asserting a verified value on `last_reply` (the only field
that survives forcing on a `container_cc` arm).

**Read `NessieAI/docs/nessie-question-set-2026-08-06.md` before running it.** That document
lists all 149 questions with their ground truth and how each was verified, the
per-family targets and the reasoning behind them, the three write/launch hazards
and what was done about each, and the cost. It is meant to be reviewed and argued
with before any paid turn.

Budget: ~**$36.54** of CC arms (153 x $0.2388 observed) plus ~5.3 hours serial.
NS arms report $0.00. Suggested `--max-usd 45`.

84 of the 149 keep their id AND their exact text from the 2026-08-06 run, 82 of
which carry a human grade, so the next report can be diffed against that one
question by question. 7 keep the id with changed text (the old grade is a
baseline, not a pre-fill) and 58 are new.

### Running only a subset

```bash
python NessieAI/tests/nessie_tests/scripts/delta_selection.py --graded <run>/grades.json
# ... run --bayesian into its OWN --out ...
git checkout NessieAI/tests/nessie_tests/corpus.json   # the narrowing is run-time state, not a commit
```

Full grading and merge procedure: `NessieAI/history/docs/nessie-corpus-additive-2026-08-06.md` (archived).

## Scope
- `--scope specific` → only `route_gate`-tagged cases (+ consistency groups).
- `--scope all` → the whole active corpus (`NessieAI/tests/nessie_tests/corpus.json`).

### `--sample` is a per-family fraction, not a count

`corpus.sample` (`corpus.py:514`) keeps `max(1, round(len(family) * ratio))`
variants per family, drawn with a seeded RNG, so the same `(ratio, seed)`
always yields the same subset. Two consequences that surprise people:

- Family sizes are wildly uneven (largest 66, smallest 2 when measured
  2026-08-24), so one flat ratio yields wildly different per-family counts.
  There is no flag that means "3 per family".
- The `max(1, …)` floor means every family contributes at least one case, so a
  small ratio over `--scope all` draws noticeably MORE than the ratio suggests:
  measured 2026-08-24, `--sample 0.1` drew 52 of 424 cases (12%), with 16 of
  the 26 families contributing exactly their floor of one.

Which cases a `(ratio, seed)` pair draws matters for more than cost: see the
next section, and print the draw before paying for it.

### What `--scope all` can write or launch

`NessieAI/docs/nessie-question-set-2026-08-06.md` §6 documents three hazards and says the
dangerous variants are **deselected**. Deselection means `is_bayesian: false`
with `status` still `active` (the doc's own disposition table says so): **it
protects `--bayesian` runs ONLY.** `--scope all` at `--tier full` selects from
the whole active corpus, hazards included, subject to `--sample`/`--seed`.
(`--scope specific` never draws them: none carries `route_gate`. A route-tier
run skips every non-gate case (`runner.py:192`), so it does not run them
either.)

In the active corpus as of 2026-08-24, outside the `--bayesian` selection but
fully inside `--scope all`:

- `pipeline.end_to_end_emit`: three turns ending in a bare `submit`. This one
  both stages a run and submits it.
- `pipeline.yes_submit_it`: a lone *"Yes, submit it."*
- `write.yes_go_ahead`: a lone *"Yes, go ahead."*, the phrasing §6 deselected
  precisely because it could push an engine through the `--confirmed-write`
  gate.
- `write.set_up_a_new_investigation_calle_2`: carries the ORIGINAL *"Set up a
  new investigation called NESSIE-PROBE-DELETEME"* text, which creates a real
  row if it fires.

(`write.set_up_a_new_investigation_calle`, no `_2`, is active AND selected,
`is_bayesian: true`, but its defusal lives in the question TEXT: §6 reworded it
to a registration that cannot complete. No harness guard is involved.)

If the deployment has a pipeline-launch backend configured, a `submit` turn
can start a REAL compute job on it, and if the write path is configured the
`entity_write` family can create real rows. Neither is theoretical: §6 records
database evidence of rows created and later removed, and in the 2026-08-06 run
both submit turns refused for question-specific reasons, not because
anything stopped them. Nor is a clean run proof of a guard: on at least one
deployment the launch path happened to fail while loading its credentials
before submission was ever reached, so an apparently-safe run may be safe only
by accident of configuration: **do not rely on your environment being
misconfigured as a safety mechanism**. Whether a given run draws any of these
variants depends on `--sample`/`--seed`, so **print the draw before running
it**:

```bash
uv run --no-project --with pydantic --with requests --with beautifulsoup4 python - <<'EOF'
from pathlib import Path
from NessieAI.tests.nessie_tests import corpus
SAMPLE, SEED = 0.1, 0            # what you are about to pass
sel = corpus.sample(corpus.select(corpus.merged(Path('NessieAI/tests/nessie_tests/corpus.json')),
                                  scope="all"), SAMPLE, SEED)
risky = [v for v in sel if v.family in ("entity_write", "pipeline_launch")]
for v in risky:
    print(v.id, "|", " / ".join(t.query for t in v.turns))
print(f"{len(sel)} cases drawn; {len(risky)} can write or launch -- read every query above.")
EOF
```

The family filter is a coarse first pass; the real check is reading the queries
it prints. (Measured while writing this: seed 2 at 0.1 draws both
`pipeline.end_to_end_emit` and `write.yes_go_ahead`.)

## Run

Every command for this harness is in `NessieAI/tests/README.md`: "Harness unit tests"
(host), "Harness container lane" (database-backed), "Harness route tier" and "Harness
full tier" (live). What follows is what those commands do not tell you.

### Unit tests: host lane (fast, no container)

Run from the **repo root**. **Baseline: everything passes except the five
machine-bound tests noted below.** Deliberately not a number. This file said
"403 passed" for three commits and was wrong by the fourth, inside its own
branch, which is the same rot the rest of this document exists to correct. Take
the count from the run (its last line), not from here.

The figure that IS pinned by a test rather than by prose is a corpus size,
but be precise about WHICH corpus size, because this file once printed "308"
here and the number rotted twice over. What
`tests/test_floor_ops.py::test_the_two_overrides_replace_in_place_and_do_not_grow_the_corpus`
pins is `corpus.curated(corpus.merged(...))`: the resolved corpus MINUS the
unreviewed `atlas`-origin variants (see `corpus.curated`'s docstring). Raw
`corpus.merged(...)` is what a run's `--scope all` actually selects from, and
it is larger by exactly the atlas set. Take both from the code:

```bash
uv run --no-project --with pydantic --with requests --with beautifulsoup4 python - <<'EOF'
from pathlib import Path
from NessieAI.tests.nessie_tests import corpus
m = corpus.merged(Path('NessieAI/tests/nessie_tests/corpus.json'))
print(len(m), "resolved (what --scope all runs);",
      len(corpus.curated(m)), "curated (what the tests pin)")
EOF
```

(2026-09-24: 474 resolved / 416 curated. If that line and the pinned test ever
disagree, this line is the stale one.)

`--no-project` and the explicit `--with` list are load-bearing, not decoration.
All three ways of getting them wrong fail in a way that does not name the cause:

- **Plain `uv run pytest` never reaches a test**, and neither does
  `cd NessieAI/tests/nessie_tests && uv run pytest tests/`: `NessieAI/tests/nessie_tests/` has no
  `pyproject.toml`, so uv resolves the repo-root project, which depends on
  `mysqlclient`, which will not build on the host
  (`Exception: Can not find valid pkg-config name`).
- **Dropping `--with orjson` runs ZERO tests.** `tests/test_v4_2_set3_replay.py`
  (and `v4_2_verifier.py`, which it imports) import `orjson` at module scope,
  so collection itself dies and pytest stops before executing anything:
  `Interrupted: 1 error during collection`. Unlike the bs4 case below, nothing
  masquerades as a test failure, but a wrapper that only greps for
  passed/failed counts sees neither and reports nothing. The tell is the single
  collection ERROR naming `test_v4_2_set3_replay.py` with
  `No module named 'orjson'`.
- **Dropping `--with beautifulsoup4` does not present as a missing dependency.**
  `NessieAI/tests/e2e/playwright/trio.py:11` imports `bs4`, and `run_case` catches
  every `Exception` as infrastructure (`runner.py:361`), so a case that hit the
  import is recorded `status="error"`. Most of the failures that follow do name
  `ModuleNotFoundError: No module named 'bs4'`, but a large minority surface as
  `AssertionError: assert 'error' == 'passed'` (or `'failed'`, `'xpass'`,
  `'no_assertions'`) and never mention bs4 at all. **The reliable tell is the
  string `bs4` appearing anywhere in the output.** No counts here on purpose:
  this bullet has pinned a pair twice and both went stale: "63 failures, 43
  naming bs4" was measured at `a85dde9` and was 431 failed / 293 passed three
  commits later. The string does not move. Do not chase the silent ones as real
  regressions.

### Known-failing everywhere but one laptop: `test_v4_2_set3_replay.py`

Five of the six tests in `NessieAI/tests/nessie_tests/tests/test_v4_2_set3_replay.py` open a
delivery zip through an absolute path into another developer's home directory:
`v4_2_verifier.py:20` pins `V13A_DELIVERY` to a `testquestions-2026-08-07` directory
under that developer's home directory.
On any machine that is not that one they die
`FileNotFoundError: ... testquestions.zip`: the same five, on every run, with
everything else green. That is the machine, not a regression; do not chase it.
(The sixth test in the file drives a synthetic producer and passes anywhere.)
The right fix is a `pytest.mark.skipif` on the delivery path's existence:
noted here, not yet applied.

### DB/contract tests: in-container lane

`NessieAI/tests/nessie_tests/tests_container/` needs Django settings and a live database, so it
cannot run on the host lane. Its command (`NessieAI/tests/README.md` "Harness container
lane") copies the tree into the app container first.

That copy is not optional and it is the whole reason this lane misleads
people: without it you are exercising the copy baked into the image at `/app`,
which is not necessarily the working tree you are editing. A green run against
a stale `/app` says nothing about your change. The old form, copying `nessie_tests`
to `/app/`, still succeeds and still tests stale code, because the image carries the
harness at `/app/NessieAI/tests/nessie_tests/`.

### Live runs

**Read "Two entry points" above first: the full tier needs the app container's
Django. Anywhere else it now refuses before the first turn instead of billing every case.**

The route gate runs from either entry point. The full pass is paid and runs ONLY via
the management command, inside the application container. Both commands are in
`NessieAI/tests/README.md` ("Harness route tier", "Harness full tier").

`--base-url` names the instance under test and `--out` decides whether the paid
evidence survives; both are covered in "Where to run this".

Before any full pass over `--scope all`, print the draw: see "What
`--scope all` can write or launch".

## Cadence
Route gate = pre-merge. Full pass = nightly / pre-release on a seeded instance.

**The route gate is cheaper, not free.** `--tier route` stops the *client*
polling once it sees `route_decided` (`http_driver.py:137-138`); it does not
cancel anything. The server started the turn on a daemon thread and returned
202, and its only early return is the `unrelated` route
(`NessieAI/cc/turn.py:350-363`), so every gate that routes
anywhere else runs to completion and bills for it after the harness has walked
away, on a CC gate a full Opus turn.

**No route is free, `unrelated` included**: it is only the cheapest. The BAML
router call (`_decide_route` → `cc_router.decide` → `_baml_decision`) is made on
every turn, and `route_decided` is emitted at `NessieAI/cc/turn.py:344` *before*
the `ROUTE_UNRELATED` check at `NessieAI/cc/turn.py:350`. `unrelated` skips the answering turn, not
the router that decided to skip it.

A case's cost is the sum of its turns, and a turn's is its router part
(`router_cost_usd` on `route_decided`) plus its engine part (`total_cost_usd` on the
turn's terminal event), over the parts observed
(`NessieAI/tests/nessie_tests/turn_cost.py:112-151`). Each turn's record is kept on
the manifest entry as `turns_meta`. Route-tier polling reaches `route_decided` and
never the terminal event, so a route run sees at most the router's price: it
reports `PARTIAL`, or `unmeasured` where the server does not price the router, and
never `$0`. An `unrelated` gate calls no engine, so its router price is its whole
cost, and a forced turn calls no router. A full-tier total is a floor whenever some
turn's part went unseen or said it was partial: a server that prices neither NS nor
router calls, or a CC turn that ends in `query_error`, which carries no cost field.
The summary counts partial and unmeasured cases, and prints how many turns had a
model fall back (`model_fallback`, `router_fallback`).

## Known-fail (RED) cases

**No variant in the resolved corpus is tagged `known_fail` any more.** None of
the variants returned by `corpus.merged(Path('NessieAI/tests/nessie_tests/corpus.json'))`
carries that tag, which is a statement about `known_fail` only; they carry
plenty of other tags, and the large majority carry an injected `route` criterion
(see "Scale" below). Four DEFINITIONS still carry `known_fail` in `corpus.json`:
`repro.parent_attr_aggregate` (#32a), `repro.thin_bundle_recall` (#32b),
`repro.eof_truncation_reporter` (the reporter EOF bonus) and
`advanced.find_me_nhp_samples_from_study_ns_graph`. But all four were retired on
2026-07-30 in the issue-#35 review, and `merged()` returns active definitions
only. `corpus.load_all_definitions()` will tell you there are four; in a run
there are none. (The whole `nessie_repro` family is retired, including
`repro.cypher_uid_dot`, which was already re-tagged `fixed` before that.)

The one `known_fail` entity a run still produces is the consistency group
`cons.nhp_sequencing_engine` (#33: the same NHP-sequencing question asked two
ways must agree on route and count). It survives because **retirement applies to
variants only**: groups are loaded separately by `load_consistency_groups()`,
which reads the `consistency_groups` block of `corpus.json` and never looks at
any variant's `status`.

`_is_real_failure` excludes an expected failure (`runner.py:549`), so
`gate_failed()` does not count it and the group does not break the gate. Two
things the tag does NOT excuse, both deliberate: a `known_fail` that PASSES is
recorded `xpass`, and one that evaluated zero criteria is recorded
`no_assertions`. Both count as real failures: the tag is a claim that the case
fails, and neither outcome demonstrated that. Remove the tag when the fix lands
(which flips the case into a real regression guard).

## Cases that asserted nothing (`no_assertions`)

Some criteria cannot be observed over HTTP and are recorded `skipped` rather than
failed: `pipeline_agent.*`, `chat_log.*`, `ui_text.*`, `op: trio_match`, and (only
on a `container_cc` turn) the four derived NS outcome fields
`api_outcome_observed` / `graph_outcome_observed` / `report_produced_output` /
`outcome_observed`. Those four read keys off `query_complete.debug`, and a CC
`query_complete` carries no `debug` at all, so they are constant-false on a CC
turn no matter what it did. The skip is conditional on the observed route: on an
NS turn the same four fields are real assertions and still fail.

**Scale, stated honestly.** Skipping them removes ONE of the reasons a CC-routed
case in a floored family goes red, not all of them. Simulate every case in the
curated corpus (the resolved corpus minus the atlas set: the frame every
figure in this section uses, because the tests that pin them use it) routing CC
and **414 of 416 are still red**, with all seven floored families at 100%. Four
criteria account for nearly all of it, and none of them is skipped: `route`
fails on **328** variants, `parser_plan.mode` on **212**, `api_ok` on **20**
and `api_plan.endpoint` on **19** (the last two fell from 128 and 104 on
2026-09-23, when the graph-answerable families stopped requiring the REST path). Those cases stay red until the corpus
itself is settled.

**Name the frame, because the two frames disagree.** Under that all-CC
simulation the skip turns *nothing* green: the green set is 2 variants with the
skip and the *same* 2 with it monkeypatched off. `tree.then_ask_about` is red
there too: its SEED turn asserts `route` nextseek_query and `parser_plan.mode`
inline, and an all-CC run fails both before the follow-up is ever reached.

The measurable payoff is the **mixed-route** case: an NS seed followed by a CC
follow-up. Since the 2026-09-24 follow-up split the corpus expects this case's
follow-up (a filter over the seed's result) on NS, so on a CC-routed follow-up the
route is now the one criterion left failing, and the NS follow-up passes, the floor
included (same test). `tree.then_ask_about`
is the only multi-turn variant in any floored family, so it is that entire
population, and in that frame it goes red -> green. Pinned by
`tests/test_evaluate.py::test_the_one_mixed_route_variant_in_a_floored_family_now_passes`,
which drives the seed NS and the follow-up CC and asserts the follow-up's
`outcome_observed` is SKIPPED rather than satisfied: `augment_debug` still
resolves it `False`, so the case is green because the criterion is no longer
scored, not because it started holding.

**Every figure above is RECOMPUTED, not remembered**, in
`tests/test_write_refusal_coverage.py`: the headline by
`test_the_cc_routing_simulation_quoted_in_the_docs_is_reproducible` (416 total /
2 green / 414 red), the four per-criterion counts by
`test_the_four_criteria_the_docs_blame_for_the_red_are_recomputed_too`, and the
two-frames claim by `test_the_cc_skip_turns_nothing_green_under_the_all_cc_simulation`.
All three drive the curated corpus through `evaluate.evaluate_turn` with the real
CC payload shape (a reply and no `debug` key), and all three fail naming this
paragraph and the matching comment in `tests/test_evaluate.py` as the places to
update. That second test exists because the headline WAS recomputed while the four
counts beside it were not, and they had drifted to 227/216/136/116: a fresh
number vouching for stale ones is worse than neither. The history of the
headline is its own warning: it read `267 of 280` until the write/delete refusal
cases were restored on 2026-08-03 (the green set did not move: all three are
red under the simulation); then `295 of 308` with 13 green until the 2026-08-06
question set gave 149 variants substantive `last_reply` assertions and collapsed
the green set to 3, because a case that asserts an ANSWER no longer passes just
because the engine said something; and this file then carried `295 of 308` for
another two weeks after the tests beside it had moved on: the guard fires when
the CORPUS changes, and cannot fire when only the prose is stale.

The skip does **not** extend to `api_ok`, `neo4j_ok`, `parser_plan.*`,
`api_plan.*` or `graph_result.*`. Those are inline, hand-written case criteria
(a case carrying them is claiming a particular engine answered it), so a CC-routed
case with an inline `api_ok` still fails. That is deliberate; changing it is a
corpus decision, not a harness one.

The guard against the obvious hazard: a case that evaluated **zero** criteria
(every one skipped, or none carried) is recorded `status="no_assertions"`, never
`passed`, and it counts as a **real failure**: a case proving nothing is corpus
drift, exactly like an `xpass`. `known_fail` does not excuse it: the tag claims
the case fails, and a case that tested nothing showed neither that nor its
absence. The rule is per CASE, not per turn: a multi-turn case that really
asserted something on one turn did assert something, and a skipped turn stays
visible in `report.html`'s observation table: each such row is classed
`skipped`, labelled `SKIPPED` in words, carries the reason that skipped it, and
is counted apart from the passes in the table's summary line, which reads
`observed (7 criteria, 2 skipped)` and never `all passed`.

As of the #35 close-out (2026-08-11) **no turn in the corpus is vacuous on any
route**: `tree.then_ask_about:follow_up` was the last one and now carries an
observable `last_reply` assertion. The skipped-row rendering above still applies
to the individual skips that remain (`chat_log.length`, and `outcome_observed` on
a container_cc arm); it is pinned by the test named below either way.

That sentence was FALSE for the whole of its first day in this file: `report.py`
never read `CriterionObservation.skipped`, a skipped row carries `passed=True`,
so a fully vacuous turn rendered as green rows inside an "all passed" count. It
is now held by
`tests/test_manifest_report.py::test_the_vacuous_turn_the_docs_promise_really_is_visible`,
which drives the real `tree.then_ask_about` through the real runner and greps
the generated HTML.

## Provider outages (GREY)
A reply carrying `NessieAI.tests.nessie_tests.outage.PROVIDER_OUTAGE_MARKER` means every
provider in an agent's fallback chain returned 503, so the turn never reached
the product. Those cases are recorded `status="error"` with `outage=True`,
reported on their own line, and excluded from the gate: an outage is not a
regression. Every *other* `error` (a dead endpoint, a timeout) still fails the
gate. Ten of the eighteen reds in the 2026-08-03 seed-6 run were one Bedrock
outage; nine were visible in the manifest and the tenth was hidden inside the
`#33` consistency group, which reports its own summary instead of its members'
replies. `run_group` checks for an outage before composing that summary.

## Comparing the graph and API agents

The graph_search follow-up (`docs/superpowers/plans/2026-09-15-graph-search-nessie.md`,
spec `docs/superpowers/specs/2026-09-15-graph-search-nessie-design.md`) runs the same
single-turn questions through the NS engine twice, once forced to the graph agent (the
model writes Cypher) and once forced to the API agent (the model writes an
`advanced_search` body), and scores both against ground truth from read-only oracles.
**Every turn is PAID and only the operator launches one**: the plan's stage P is the
runbook, with each step's turn count, cap and stop rules. No agent and no CI job starts
a turn; the tests of everything below drive fakes.

**The two forces.** Both ride on the chat request and both are admin-only server side.

- `force_route` (`ns` or `cc`) sends the turn to one engine instead of the router.
  A non-superuser's value is dropped without a word.
- `force_parser_mode` (`graph` or `api`) forces the NS parser. `_force_parser_mode` in
  `NessieAI/chat_nextseek/src/chat_nextseek/agents/parser.py` runs last among the
  parser's guardrails: `graph` turns `new_search` into `graph_query`, and `api` turns
  `graph_query` into `new_search` on the parser's first REST candidate, else
  `advanced_search`. Every other parser mode is left as the parser chose. It is
  honoured only for a superuser on a process that sets `NEXTSEEK_EVAL_PARSER_FORCE=1`
  (only the venue sets it), and dropped without a word otherwise. A landed force
  appends a note containing `by the evaluation switch`, and the parser's own choice,
  to `parser_plan.notes`.
- `prompt_variant` (`v2`, `v2_apoc` or `v3`) runs the NS agents on an alternative prompt set,
  `NessieAI/chat_nextseek/src/chat_nextseek/prompts/variants/<name>/`, with or without
  `force_parser_mode`. Same gate and same silent drop. A turn that ran it records
  `debug.prompt_variant` and `debug.prompt_variant_files`.

**The flags.**

| Flag | Entry point | What it does |
|---|---|---|
| `--force-route {ns,cc}` | both | forces every turn of a normal run; the `route`, `engine` and `route_source` criteria are stripped (`runner.STRIPPED_UNDER_FORCING`) |
| `--force-parser-mode {graph,api}` | both | needs `--force-route ns`; the module CLI refuses both force flags with `--bayesian` |
| `--prompt-variant {v2,v2_apoc,v3}` | both | needs `--force-route ns`; with `--arms` it rides on every arm and the preflight's parser probes, is recorded in `arms.json` `run_meta.prompt_variant` and every payload, and a `--resume` with another variant is refused |
| `--arms graph,api`, `--arms graph` or `--arms auto` | `manage.py nessie` | calls `runner.run_arms`; needs `--cases`, `--force-route ns` and `--tier full`, and excludes `--force-parser-mode`. `auto` forces the route only: the parser picks graph or API per question, and `debug.parser_plan.mode` in each payload says which |
| `--resume` | `manage.py nessie`, with `--arms` | continues the run in `--out`, skipping every (question, arm) it holds except a provider outage; refuses a changed cases file or arm list |
| `--max-turns N` | `manage.py nessie`, with `--arms` | the turns this invocation may drive; it stops before a question that would exceed it, and 0 runs only the preflight |
| `--password-env NAME` | `manage.py nessie` | reads the password from that environment variable and never prints it |

**The arms.** `runner.ARM_PRESETS` defines `graph` (`force_route` `ns`,
`force_parser_mode` `graph`) and `api` (`ns`, `api`). Per question, every arm runs back
to back, each turn in a fresh session through the same `run_case` every run uses, and
the first arm rotates with the question's index in the cases file. Before any
question, the preflight spends one forced-route probe
(`preflight.assert_force_route_works`) and one full turn per arm
(`preflight.assert_parser_force_works`). The second checks the route force, the note,
the plan's mode and, on the graph arm, that `debug.graph_context` is `catalog`: a
`fallback` turn read the committed schema file instead of the live catalog, which
voids the graph arm. A refusal stops the run before any question and names its
remedy. A wrong answer is the measurement, so an arms run never exits non-zero for one.

**What an arms run writes** under `--out`, rewritten after every (question, arm), files
mode 600 in mode 700 directories:

- `arms.json`: `run_meta` (git sha, corpus fingerprint, the cases file's sha256, the
  arms, the base URL, the preflight's pass), `progress`, and one row per question with
  each arm's status, task ids and elapsed seconds;
- `<arm>/manifest.json` and `<arm>/report.html`, the shapes every run writes;
- `<arm>/payloads/<id>/<turn>.json`: each turn's final payload (the query, the task and
  session ids, the route observation, `query_complete` and the elapsed time), which is
  what the scorer reads.

The venue runs a `git archive` snapshot with no `.git`, so `runner.git_sha` falls back
to a `SNAPSHOT` file at the repository root.

**Ground truth and the scorer.** All free, on the host, except the oracles.

- `engine_truth.py`: the truth-file models (`TruthFile`, `TruthQuestion`, `Expected`
  with its alternate readings) and `reply_satisfies`, which checks a reply for the
  expected numbers or items.
- `scripts/build_engine_cases.py`: truth skeletons from the question selection
  (`--skeleton`, `--questions`), and, from the filled truth, the cases files the arms
  run (`--cases` writes `group-<g>.json`, `pilot-<g>.json` and `rest-<g>.json`, with
  engine-neutral criteria only).
- `scripts/derive_truth.py`: fills a truth file by running its oracles inside the
  venue (a graph_search request, Cypher in a READ transaction, SQL inside a read-only
  transaction, or a measured number); `--summary` totals the files, and
  `--fingerprint-only` exits non-zero when the graph changed since the truth was
  derived.
- `engine_compare.py`: `uv run --no-project --with pydantic python -m
  NessieAI.tests.nessie_tests.engine_compare --group a|b --run DIR [--run DIR ...]
  --truth DIR --outputs DIR --prices FILE --out DIR` judges every stage of every turn
  (route, switch, context, entities, parser, request, engine value, reply) and writes
  `compare.json`, `compare.md` (the verdict first) and `questions.csv`. `--cost-only`
  and `--checks` print and write nothing.

A manifest from a server that does not price NS turns carries no cost for them
("Cadence" above), so for such a run the scorer is the only place they are priced. chat_nextseek appends one line per model call to a single
`llm_calls.jsonl` under the process's `LOG_DIR`, not one per run, so the scorer gives a
turn the ledger lines from its run-root timestamp to that plus its elapsed time and
`engine_compare.LEDGER_SLACK_S`, priced with the operator's price table. The arms run
back to back, so a quick first call of the next turn can land in that slack and count
on both turns.

**Where it lives.** Everything these runs read or write is under the operator's
`$GS_WORK/nessie/`, outside the repository, and is never tracked: some questions name
real people. That is the question selection, `truth/`, `cases/`, one directory per
run under `runs/`, the price table, and the venue's snapshot, outputs and logs under
`venue/`. The venue, `scripts/graph_search/nessie_venue.sh` (its row is in
`scripts/graph_search/README.md`), is a throwaway app container that runs a snapshot
of HEAD against the live databases with the flag set; its `run` and `bg` wrap
`manage.py nessie --tier full --user demo --password-env GS_DEMO_PASSWORD`. Only the
operator starts it. The lane row is in `NessieAI/tests/README.md`.

## Depends on / depended on by

**Depended on by: application code, which makes this directory load-bearing.**
- The Container-CC paired-evidence ingester imports four modules of it (`NessieAI/cc/op_registry/paired_evidence.py:37-40`), so removing one breaks the op registry. A frozen engine-to-harness edge.
- `manage.py nessie` imports the driver and runner at call time (`nextseek_api/management/commands/nessie.py:96`), which is how a run happens inside the trusted Django process.
- Task-family labels are read straight out of the corpus file, resolved through `NessieAI/paths.py` (`NessieAI/router/family_labels.py:29`), so moving `corpus.json` without `paths.py` empties that catalog, and the router logs the missing file rather than failing.
- The `route_capabilities` generator imports three modules and resolves the corpus through `NessieAI/paths.py` (`NessieAI/build_tools/gen_op_surfaces/route_capabilities.py:29-31`, `NessieAI/build_tools/gen_op_surfaces/route_capabilities.py:39`). A frozen edge.
- Human-grade fitting imports the paired manifest model, lazily (`NessieAI/hibayes/human_grade_fit.py:704`). A frozen edge.

The plan018 verifier script and the owned-surface and mutation tooling that once keyed
on paths in this directory are archived in `NessieAI/history/plan018/scripts/`; they no
longer govern it.

**Depends on:**
- The E2E criterion DSL in `NessieAI/tests/e2e/`, imported by package (`NessieAI/tests/nessie_tests/corpus.py:7-8`, `NessieAI/tests/nessie_tests/evaluate.py:7`).
- `pydantic`, at module scope in the two manifest models and the verifier (`NessieAI/tests/nessie_tests/manifest.py:4`, `NessieAI/tests/nessie_tests/bayes_manifest.py:13`, `NessieAI/tests/nessie_tests/v4_2_verifier.py:15`).
- `orjson`, at module scope in the verifier only (`NessieAI/tests/nessie_tests/v4_2_verifier.py:14`).
- Three heavy dependencies are deliberately lazy, imported inside the function that needs them: Django models (`NessieAI/tests/nessie_tests/bundle.py:56`, `NessieAI/tests/nessie_tests/bundle.py:77`), `openpyxl` (`NessieAI/tests/nessie_tests/evaluate.py:148`) and `zstandard` (`NessieAI/tests/nessie_tests/collect.py:177`).
- The live HTTP endpoint and its progress route, by string rather than by import (`NessieAI/tests/nessie_tests/http_driver.py:7`, `NessieAI/tests/nessie_tests/http_driver.py:41`, `NessieAI/tests/nessie_tests/http_driver.py:49`).
- The `nextseek` container and the `docker` binary, for the paired run's post-hoc reads only (`NessieAI/tests/nessie_tests/sources.py:104-105`).

**What is deliberately not in those lists.** Two kinds of hit were excluded.
One: matches inside `NessieAI/history/` and `NessieAI/docs/`, which are dated records
naming these paths rather than code that runs them. Two: comments that mention the
package without depending on it (in `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py`,
`nextseek_api/assistant/models_db.py:327` and
`NessieAI/tests/cc/test_cc_session_metas_columns.py:220`). One hit
looks like an outbound Django dependency and is not: the line importing
`nextseek_api.assistant.models_db` at `NessieAI/tests/nessie_tests/sources.py:253` sits inside
the `_CONTAINER_PY` string opened at `NessieAI/tests/nessie_tests/sources.py:245`, which is
probe source injected into a container, not an import this file performs. Test
modules that import the package from elsewhere in the tree are omitted from the
inbound list on the grounds that they are tests of it rather than consumers of
it; they are in `NessieAI/tests/router/test_route_capabilities.py:31-33`,
`NessieAI/tests/cc/test_paired_evidence.py:15-16` and
`NessieAI/tests/hibayes/test_task2_coverage_edges.py:11`.
