# ci/: what will bite you

## Invariants

- `ci/routes.py` may import the standard library and nothing else. An AST walk of
  the file returns exactly five import statements, all stdlib
  (`ci/routes.py:15-20`), and the module docstring says why
  (`ci/routes.py:1-14`). Add a third-party import and the smoke lane stops
  collecting: it runs under `uv run --no-project` with pytest, requests and
  playwright and nothing else (`.github/workflows/ci-smoke.yml:112-114`). The
  gate lane will not warn you, because it runs in the application's own
  environment, whose dependency list includes requests (`pyproject.toml:92`).
- Django is imported inside `_walk()` and `live_patterns()`, never at module
  scope. A recursive grep for `django` over `ci/`, ignoring this document,
  returns four lines, and the only two that are executable code are those
  function-scope imports (`ci/gate/live_routes.py:93`,
  `ci/gate/live_routes.py:113`). Hoist either to module scope and the whole
  no-stack smoke lane stops collecting, because
  `ci/smoke/test_registry_contents.py:23` imports `suggest_path` from that module
  in an environment that has no Django at all.
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
  the status it returns while broken (`ci/routes.py:44-49`). Declare today's
  broken status instead and the `xfail` reports green while the defect stands and
  red on the day somebody fixes it, both signals inverted.
- An absent `CI_BOX_PROFILE` resolves to `prod`, the most restrictive profile,
  and `--profile` can only narrow from there (`ci/smoke/conftest.py:149-152`,
  `ci/smoke/conftest.py:168-177`). Change that default to anything else and an
  unconfigured box silently gains the right to issue writes.
- Every pattern in `REGISTRY` appears exactly once, enforced at import time
  (`ci/routes.py:882-899`, `ci/routes.py:924`). A duplicate makes the second
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
- `seek/tests/test_graph_search_js.py` skips every test where `node` is missing,
  and the application image has no node, so the gate lane reports them skipped,
  never failed or passed. The blocking unit tests step fails on a runner without
  node for that reason. To run them, use a host with node:
  `node seek/tests/js/graph_search_cases.js`, or that module under
  `uv run --no-project --with pytest pytest`, which needs no Django.
- No step runs `makemigrations --check`. Under `dmac.test_settings`,
  `manage.py makemigrations --check --dry-run` stops on system check
  `4_0.E001`: `dmac/settings.py` splits an unset `DJANGO_CSRF_TRUSTED_ORIGINS`
  into one empty origin. With `--skip-checks` it runs, and reports changes the
  tree has not migrated: in Mezzanine's own apps (its installed migrations lag
  the installed Django), in `seek`, and in `nextseek_api`, whose TurnLedger index
  keeps the migrated name that `nextseek_api/migrations/_turn_ledger_heal.py`
  converges live databases to, not the name the model now generates. Run it in
  the gate lane with `--skip-checks` for the current list. Adding it to the
  blocking step turns the job red on every run until those are settled.
- `OWNED_ROUTE_COUNT` in `ci/smoke/test_registry_contents.py` is a
  second, hand-maintained declaration of the route count, and its own comment
  names the completeness gate as the authority. Add a route and this constant
  goes red in a lane that cannot tell you whether the number is right, because
  that lane has no resolver to ask.
- `EXCLUDE_DEAD` and `EXCLUDE_ADMIN` are declared at `ci/routes.py:24-30` but no
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
