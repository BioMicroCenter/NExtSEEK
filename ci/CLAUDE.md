# ci/ — what will bite you

## Invariants

- `ci/routes.py` may import the standard library and nothing else. An AST walk of
  the file returns exactly five import statements, all stdlib
  (`ci/routes.py:15-20`), and the module docstring says why
  (`ci/routes.py:1-14`). Add a third-party import and the smoke lane stops
  collecting: it runs under `uv run --no-project` with pytest, requests and
  playwright and nothing else (`.github/workflows/ci-smoke.yml:101-103`). The
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
- A route's `expect` records the status the route returns when it works, never
  the status it returns while broken (`ci/routes.py:44-49`). Declare today's
  broken status instead and the `xfail` reports green while the defect stands and
  red on the day somebody fixes it — both signals inverted.
- An absent `CI_BOX_PROFILE` resolves to `prod`, the most restrictive profile,
  and `--profile` can only narrow from there (`ci/smoke/conftest.py:142-145`,
  `ci/smoke/conftest.py:162-170`). Change that default to anything else and an
  unconfigured box silently gains the right to issue writes.
- Every pattern in `REGISTRY` appears exactly once, enforced at import time
  (`ci/routes.py:882-899`, `ci/routes.py:924`). A duplicate makes the second
  entry's profiles, methods and exclusions unreachable through `match()`, which
  reads as a route being permitted when the author thought it was excluded.
- Regenerate the baseline with `ci/diff_baseline.py --emit-baseline`, never with
  a hand-written grep (`ci/diff_baseline.py:77-80`). One parser on both sides is
  what stops the recorded ids and the parsed ids drifting into disagreement.

## Landmines

- The read-only mount in the gate recipe works ONLY because the recipe's first
  line pre-creates two directories on the host (`ci/gate/live_routes.py:16`).
  Skip that `mkdir` and Django dies during settings import, before a single test
  is collected: `dmac/settings.py:498` calls `os.makedirs` on a path inside the
  mount. Measured 2026-09-03 by mounting an empty directory over
  `/src/schema_rag`: `OSError: [Errno 30] Read-only file system:
  '/src/schema_rag/duckdb'`. Pre-creating them is what makes the read-only mount
  work at all, because `os.makedirs(..., exist_ok=True)` swallows the read-only
  failure once the directory it wanted is already there.
- `ci/pytest-baseline.txt` is STALE against this branch. Measured 2026-09-03 by
  running the six lane directories its own header declares
  (`ci/pytest-baseline.txt:7-11`) inside the application image over a writable
  copy of this worktree, then diffing with `ci/diff_baseline.py`: 57 ids
  failed that the baseline does not list, and 12 ids it lists now pass. A second
  run under a different mount path reproduced 55 of the 57 exactly (the other two
  are outside the two directories that run covered), so they are not an artifact
  of where the tree was mounted. The 57 are 35 under `seek/` and 20 under
  `nextseek_api/`, plus one each under `scripts/` and `startup/`; the file records
  only 2 `seek/` entries in total. Anyone reading a red pytest job on this branch
  triages all 57 by hand; the repo-root session-report index carries the only
  line in the tree matching "Of the 57", found by grepping every file outside
  `.git`, and it records that just 3 of them are real.
- The baseline is valid for one exact command and one tree state, and says so
  (`ci/pytest-baseline.txt:3-5`). Its header still names the measurement it was
  taken from (`ci/pytest-baseline.txt:19-21`), which is what tells you it has not
  been regenerated since. Diff a run of any other lane set against it and every
  difference is noise.
- It is nonetheless still accurate for two boundaries: the 2026-09-03 diff
  produced zero new failures under `build_tools/` and zero under `chat_nextseek/`.
  A sibling document citing `ci/pytest-baseline.txt:27` for a known
  `build_tools` failure is right, and that entry still failed in this run.
  Reading either result as a verdict on the whole file misleads you in one
  direction or the other, so diff the run yourself and read only your own rows.
- `uv sync` cannot build this project on a host without MySQL client headers.
  `uv.lock:1754-1757` lists exactly two `mysqlclient` wheels, both `win_amd64`,
  so every Linux install builds from the sdist at `uv.lock:1753`. Measured
  2026-09-03 on this host: `Exception: Can not find valid pkg-config name.` That
  is why the gate lane is a container recipe and not a host `pytest` invocation.
- Do not trust `.github/workflows/ci-pytest.yml:34-36`, which claims every
  dependency resolves as a wheel, mysqlclient included. `uv.lock:1754-1757`
  contradicts it: that job succeeds because the GitHub runner image already
  carries the client headers, not because a wheel exists. Move that job to a
  slimmer container on the strength of that comment and `uv sync` fails at step
  one.
- `ci/smoke/README.md:36-39` names six files as the no-stack lane and its
  copy-paste command at `ci/smoke/README.md:41-46` lists the same six. There is a
  seventh: `ci/smoke/test_terminal_unit.py:1` declares itself stack-free and, run
  alone on 2026-09-03 with no stack and no credentials, gave 7 passed in 0.01s.
  Paste the documented command and you silently skip those 7.
- Nothing under `ci/` or `ci/gate/` configures pytest. A find for `conftest.py`
  or `pytest.ini` anywhere beneath `ci/` returns only `ci/smoke/conftest.py` and
  `ci/smoke/pytest.ini`, so `pytest ci/gate` takes its configuration from the
  repo-root `pyproject.toml:146-148`, which names the real `dmac.settings`. Both
  callers therefore pass the test settings module in the environment
  (`.github/workflows/ci-pytest.yml:82`). Measured 2026-09-03 with that variable
  removed: 2 failed, 3 passed, both gate tests dying on
  `AttributeError: 'Settings' object has no attribute 'NEO4J_DATABASE'`, a value
  that normally arrives from the gitignored `dmac/local_settings.py`.
- `ci/diff_baseline.py` always exits 0, by decision (`ci/diff_baseline.py:8-9`,
  `ci/diff_baseline.py:131-132`). A wrapper that treats its exit code as a
  verdict will call every run a pass, including one that reports 57 new failures.
  Only the gate step decides that job's outcome
  (`.github/workflows/ci-pytest.yml:74-79`).
- `OWNED_ROUTE_COUNT = 157` at `ci/smoke/test_registry_contents.py:37` is a
  second, hand-maintained declaration of the route count, and its own comment
  names the completeness gate as the authority
  (`ci/smoke/test_registry_contents.py:28-36`). Add a route and this constant
  goes red in a lane that cannot tell you whether the number is right, because
  that lane has no resolver to ask.
- One entry in the baseline can never be cleared anywhere. Its own comment
  records that `nextseek_api/attributes/tests` is an evidence lane which cannot
  run off its author's machine, and names that machine's home directory
  (`ci/pytest-baseline.txt:22-24`). It is the only match for a developer's home
  path anywhere under `ci/`, found by grepping the whole boundary for `/home/`
  followed by a lower-case letter, and it costs this boundary's own lanes
  nothing: both ran clean here on 2026-09-03. What it costs is the baseline,
  which keeps a collection-level entry that no run on any other machine can
  shrink away.
- `EXCLUDE_DEAD` and `EXCLUDE_ADMIN` are declared at `ci/routes.py:24-30` but no
  entry uses either: counting the `exclude` values across `REGISTRY` on
  2026-09-03 gives `EXCLUDE_UNSAFE_METHOD` 13, `EXCLUDE_COST` 12 and
  `EXCLUDE_EXTERNAL` 1, totalling all 26 excluded entries. Reading the code list
  as a description of what the registry actually excludes will mislead you.

## Test command

The gate is the lane that blocks, and the no-stack smoke lane is the one that
needs nothing at all; both are in `ci/README.md` with their commands and their
2026-09-03 numbers. The lane whose result is the headline is the baseline lane,
which was reproduced on 2026-09-03 inside the application image over a writable
copy of this worktree, after generating the BAML client the way
`.github/workflows/ci-pytest.yml:42-43` does:

```bash
docker run --rm -i -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  -e PYTHONDONTWRITEBYTECODE=1 -v "$COPY":/src -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest nextseek_api seek chat_nextseek startup \
  build_tools scripts --continue-on-collection-errors -q -p no:cacheprovider
```

2026-09-03: **273 failed, 8195 passed, 80 skipped, 8 xfailed, 73 errors in
194.94s**, which `ci/diff_baseline.py` scored as 57 new and 12 fixed against
`ci/pytest-baseline.txt`. Mount a writable COPY rather than the worktree: the
run has to generate the gitignored BAML client into the tree first
(`.github/workflows/ci-pytest.yml:42-43`), and a fresh checkout has none — a
`ls -d` for `dmac_assistant/src/dmac_assistant/router/baml_client` and
`dmac_assistant/tools/e2e/baml_client`, the two directories that generator
writes, finds neither in this worktree.

## See also

- See `ci/README.md` for what each module does and how the two lanes are wired.
- See `ci/smoke/README.md` for the smoke suite's flags, profiles, credentials and
  known conditions.
- See `startup/README.md` for the `./startup.sh ci` operator entry point.
- See `DEPLOYMENT.md` for where a failing post-deploy run leaves you.
