# ci/: what will bite you

## Invariants

- `ci/routes.py` may import the standard library and nothing else. An AST walk of
  the file returns exactly five import statements, all stdlib
  (`ci/routes.py:20-25`), and the module docstring says why
  (`ci/routes.py:1-19`). Add a third-party import and the smoke lane stops
  collecting: it runs under `uv run --no-project` with pytest, requests and
  playwright and nothing else (`.github/workflows/ci-smoke.yml:112-114`). The
  gate lane will not warn you, because it runs in the application's own
  environment, whose dependency list includes requests (`pyproject.toml:92`).
- Django is imported inside `_walk()` and `_owned_leaves()`, never at module
  scope: `grep -rn "import django\|from django" ci/ --include=*.py` returns those
  two lines and nothing else (`ci/gate/live_routes.py:99`,
  `ci/gate/live_routes.py:119`). Hoist either to module scope and the whole
  no-stack smoke lane stops collecting, because
  `ci/smoke/test_registry_contents.py:25` imports `suggest_path` from that module
  in an environment that has no Django at all. `live_views()` reads each view off
  the same walk, so it is bound by the same rule.
- Every entry says what a request there writes: `effect`, and for `writes` the
  `ci/writers.py` ids that do it (`ci/routes.py:77-78`, validated at
  `ci/routes.py:126-152`). The field DEFAULTS to `reads`, because a dataclass
  cannot tell an author who means it from one who said nothing; what forces a new
  route to be classified is the gate's paste-ready skeleton, which emits
  `effect="UNCLASSIFIED"` for `Route` to refuse
  (`ci/gate/test_route_registry.py`). Make that default anything else valid and a
  route nobody read starts claiming an effect nobody checked.
- `effect="n/a"` means the route is not this application's surface, and
  `ci/smoke/test_registry_contents.py` pins it to exactly the `resolver=False`
  entries: the nginx-served asset and the Django admin's own login. Classify a
  route of ours `n/a` and the writer registry stops asking it anything.
- `ci/blocking_lanes.py` may import the standard library and nothing else. The
  "Blocking unit tests (ci/blocking_lanes.py)" step of
  `.github/workflows/ci-pytest.yml` runs it with the runner's bare `python`,
  outside the application's environment, so a third-party import fails that step
  before a single test runs. Its gate test, `ci/gate/test_blocking_lanes.py`,
  keeps to the same rule.
- Every glob in `BLOCKING_GLOBS` matches at least one file, and `main()` exits 1
  with nothing on stdout when one does not (`ci/gate/test_blocking_lanes.py`).
  Pytest given no path walks the whole tree, so an empty list must stop the
  step, never reach pytest.
- A module a blocking glob matches blocks every run from the commit that adds
  it, so it must pass in the no-stack lane: SQLite in memory, no network, no
  MySQL, no Neo4j. A graph test that needs a live service either skips itself
  without that service or takes a name outside the globs.
- A route's `expect` records the status the route returns when it works, never
  the status it returns while broken (`ci/routes.py:79-84`). Declare today's
  broken status instead and the `xfail` reports green while the defect stands and
  red on the day somebody fixes it, both signals inverted.
- An absent `CI_BOX_PROFILE` resolves to `prod`, the most restrictive profile,
  and `--profile` can only narrow from there (`ci/smoke/conftest.py:149-152`,
  `ci/smoke/conftest.py:168-177`). Change that default to anything else and an
  unconfigured box silently gains the right to issue writes.
- Every pattern in `REGISTRY` appears exactly once, enforced at import time
  (`ci/routes.py:1191-1208`, `ci/routes.py:1238`). A duplicate makes the second
  entry's profiles, methods and exclusions unreachable through `match()`, which
  reads as a route being permitted when the author thought it was excluded.
- Regenerate the baseline with `ci/diff_baseline.py --emit-baseline`, never with
  a hand-written grep (`ci/diff_baseline.py:77-80`). One parser on both sides is
  what stops the recorded ids and the parsed ids drifting into disagreement.

## Landmines

- Never opt out of the Nessie lane with `-m`: any `-m` expression switches the
  write lane on. Use `--no-nessie`. `pytest_collection_modifyitems` returns early
  whenever `-m` is given, and that early return is the only thing that keeps the
  write lane deselected (`ci/smoke/conftest.py:261-266`). The Nessie switches are
  applied before it, which is why they are options and not marker expressions
  (`ci/smoke/conftest.py:209-223`). So `-m "not nessie"` reads like a narrower
  run and in fact runs the write lane as the superuser.
- The read-only mount in the gate recipe works ONLY because the recipe's first
  line pre-creates two directories on the host (`ci/gate/live_routes.py:16`).
  Skip that `mkdir` and Django dies during settings import, before a single test
  is collected: `dmac/settings.py:507` calls `os.makedirs` on a path inside the
  mount. Measured 2026-09-03 by mounting an empty directory over
  `/src/schema_rag`: `OSError: [Errno 30] Read-only file system:
  '/src/schema_rag/duckdb'`. Pre-creating them is what makes the read-only mount
  work at all, because `os.makedirs(..., exist_ok=True)` swallows the read-only
  failure once the directory it wanted is already there.
- `ci/pytest-baseline.txt` was regenerated for the NessieAI move in the gate lane
  (the application image, the tree mounted read-only, `--network none`), not on a
  CI runner, and its header says so (`ci/pytest-baseline.txt:22-30`). That lane
  cannot generate the BAML client, so the first GitHub run after the move, which
  does generate it, reports the entries the missing client broke as fixed and the
  tests that lane could not collect, but that fail anyway, as new. Regenerate the
  file from that run's output with `ci/diff_baseline.py <run output> --emit-baseline`
  (`ci/diff_baseline.py:77-80`), never by editing entries by hand.
- The baseline is valid for one exact command and one tree state, and says so
  (`ci/pytest-baseline.txt:3-5`). Diff a run of any other lane set against it and
  every difference is noise, so diff the run yourself and read only your own rows.
- `uv sync` cannot build this project on a host without MySQL client headers.
  `uv.lock:1754-1757` lists exactly two `mysqlclient` wheels, both `win_amd64`,
  so every Linux install builds from the sdist at `uv.lock:1753`. Measured
  2026-09-03 on this host: `Exception: Can not find valid pkg-config name.` That
  is why the gate lane is a container recipe and not a host `pytest` invocation.
- Do not trust `.github/workflows/ci-pytest.yml:37-39`, which claims every
  dependency resolves as a wheel, mysqlclient included. `uv.lock:1754-1757`
  contradicts it: that job succeeds because the GitHub runner image already
  carries the client headers, not because a wheel exists. Move that job to a
  slimmer container on the strength of that comment and `uv sync` fails at step
  one.
- Nothing under `ci/` or `ci/gate/` configures pytest. A find for `conftest.py`
  or `pytest.ini` anywhere beneath `ci/` returns only `ci/smoke/conftest.py` and
  `ci/smoke/pytest.ini`, so `pytest ci/gate` takes its configuration from the
  repo-root `pyproject.toml:146-148`, which names the real `dmac.settings`. Both
  callers therefore pass the test settings module in the environment
  (the gate step's `env:` in `.github/workflows/ci-pytest.yml`). Measured 2026-09-03 with that variable
  removed: 2 failed, 3 passed, both gate tests dying on
  `AttributeError: 'Settings' object has no attribute 'NEO4J_DATABASE'`, a value
  that normally arrives from the gitignored `dmac/local_settings.py`.
- `ci/diff_baseline.py` always exits 0, by decision (`ci/diff_baseline.py:8-9`,
  `ci/diff_baseline.py:131-132`). A wrapper that treats its exit code as a
  verdict will call every run a pass, including one that reports new failures.
  Only the gate step, the blocking unit tests step, and a lanes step whose
  pytest did not run at all can fail that job (the comment above the gate step
  in `.github/workflows/ci-pytest.yml`).
- `seek/tests/test_sample_search_js.py` skips every test where `node` is missing,
  and the application image has no node, so the gate lane reports them skipped,
  never failed or passed. The blocking unit tests step fails on a runner without
  node for that reason. To run them, use a host with node:
  `node seek/tests/js/sample_search_cases.js`, or that module under
  `uv run --no-project --with pytest pytest`, which needs no Django.
- **`makemigrations --check` blocks for `nextseek_api` only, and the scope is
  deliberate.** Under `dmac.test_settings` the check needs `--skip-checks`,
  because `dmac/settings.py` splits an unset `DJANGO_CSRF_TRUSTED_ORIGINS` into
  one empty origin and Django stops on system check `4_0.E001` before it reaches
  migration state. The `nextseek_api` TurnLedger index now declares the name its
  migration created and `nextseek_api/migrations/_turn_ledger_heal.py` converges
  live databases to, so that app is clean. The other apps are not, for two
  different reasons:
  - Mezzanine's `blog`, `core`, `generic` and `pages` propose migrations whose
    files would be written **into `site-packages`**, because its shipped
    migrations lag the installed Django. Nothing here can fix them and `uv`
    would overwrite an edit. They can never join the check.
  - `seek` proposes creating five models whose DDL the installer applies out of
    band. Three are `managed = False` as the invariant in `seek/CLAUDE.md`
    requires; `Sample_types_context` and `Session_state` are `managed = True`,
    which is the condition that file warns about. `seek` joins the check only
    after that is ruled on.
  Measurements and the full output:
  `docs/superpowers/plans/2026-09-16-ci-coverage-gaps-findings.md`. Widen the
  scope by naming another app once it is green, never by dropping the argument.
- `OWNED_ROUTE_COUNT` in `ci/smoke/test_registry_contents.py` is a
  second, hand-maintained declaration of the route count, and its own comment
  names the completeness gate as the authority. Add a route and this constant
  goes red in a lane that cannot tell you whether the number is right, because
  that lane has no resolver to ask.
- The tripwire in `ci/gate/test_route_effects.py` REPORTS; it does not fail. It
  walks from a route's view to the writer sites by NAME, three calls deep, and
  both of its bounds are load-bearing and measured. It follows a call only where
  exactly ONE function of that name exists in the scanned tree: at twelve, it
  reported almost every `/seek/` page as reaching WR-14 and WR-15, because the
  legacy table classes spell their writes `new`, `update` and `delete`. And it
  drops a name more than 20 sites call: 158 sites call something `.execute(...)`,
  almost always a database cursor, while the tree holds exactly one function
  named `execute`, so following it connected every read-only SQL view to batch
  upload's inserts. Raise either bound and the report becomes noise a reader
  learns to skip. It under-reaches by design -- an overloaded name, a variable, a
  string dispatch, a decorator that does not set `__wrapped__` -- so read its
  output as routes worth re-reading, never as a verdict. Its one assertion is
  that the Sample Search page is not in the list, which catches a walk gone loose.
- `EXCLUDE_DEAD` and `EXCLUDE_ADMIN` are declared at `ci/routes.py:29-35` but no
  entry uses either: counting the `exclude` values across `REGISTRY` on
  2026-09-03 gives `EXCLUDE_UNSAFE_METHOD` 13, `EXCLUDE_COST` 12 and
  `EXCLUDE_EXTERNAL` 1, totalling all 26 excluded entries. Reading the code list
  as a description of what the registry actually excludes will mislead you.

## Test command

The gate is the lane that blocks, and the no-stack smoke lane is the one that
needs nothing at all; both are in `ci/README.md` with their commands. The lane
whose result is the headline is the baseline lane. To reproduce it inside the
application image, mount a writable copy of this worktree and generate the BAML
client first, the way `.github/workflows/ci-pytest.yml:45-46` does:

```bash
docker run --rm -i -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  -e PYTHONDONTWRITEBYTECODE=1 -v "$COPY":/src -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest nextseek_api seek startup scripts \
  NessieAI/tests/cc NessieAI/tests/router NessieAI/tests/hibayes \
  NessieAI/tests/ns NessieAI/tests/api NessieAI/tests/schema_rag \
  NessieAI/tests/chat_nextseek NessieAI/tests/build_tools \
  --continue-on-collection-errors -q -p no:cacheprovider
```

Score the run with `ci/diff_baseline.py` against `ci/pytest-baseline.txt`. Mount
a writable COPY rather than the worktree: the
run has to generate the gitignored BAML client into the tree first
(`.github/workflows/ci-pytest.yml:45-46`), and a fresh checkout has none: a
`ls -d` for `NessieAI/dmac_assistant/src/dmac_assistant/router/baml_client` and
`NessieAI/dmac_assistant/tools/e2e/baml_client`, the two directories that generator
writes, finds neither in this worktree.

## See also

- See `ci/README.md` for what each module does and how the two lanes are wired.
- See `ci/smoke/README.md` for the smoke suite's flags, profiles, credentials and
  known conditions.
- See `startup/README.md` for the `./startup.sh ci` operator entry point.
- See `DEPLOYMENT.md` for where a failing post-deploy run leaves you.
