# ci/ — the CI lane

## What this is

One idea in four artifacts: every application URL is **declared once**, in
Python, and everything else is derived from that declaration. `ci/routes.py`
holds the declaration. Two pytest lanes read it: a gate that runs inside the
application's own environment, where Django is importable, and a smoke suite that
runs outside the container in an isolated environment holding pytest, requests
and playwright and nothing else. A committed
text file, `ci/pytest-baseline.txt`, is not code at all: it is a recorded
measurement of which tests were already failing, so that a run can report what is
*new* rather than what is *red*.

The declaration is what makes running against a production instance defensible.
A route that nobody declared is refused before the request is built, so dangerous
surface is excluded because nobody opted it in rather than because somebody
remembered to list it (`ci/routes.py:10-13`, `ci/smoke/client.py:84-89`).

Nothing in the Django application imports this package. It is a consumer of the
application, never a dependency of it.

## Surface

This boundary is not a library with public entry points that the application
calls. Its surface has three different shapes, and the edges below were derived
mechanically rather than recalled:

- **an importable module** — `ci/routes.py` and `ci/gate/live_routes.py`, whose
  surface is their public names;
- **two pytest suites invoked by path** — `ci/gate/` and `ci/smoke/`, whose
  surface is the command that runs them and the environment it needs;
- **two committed data artifacts** — `ci/pytest-baseline.txt` and the `REGISTRY`
  list itself, whose surface is the format and the command that regenerates them.

| file | what it holds |
|---|---|
| `ci/routes.py` | `Route`, `REGISTRY`, `match()`, `PLACEHOLDERS`, `PROFILES`, `EXCLUDE_CODES` |
| `ci/gate/live_routes.py` | `live_patterns()`, `suggest_path()` |
| `ci/gate/test_route_registry.py` | the two blocking completeness tests |
| `ci/gate/test_live_routes_unit.py` | pure-string tests for `suggest_path` |
| `ci/diff_baseline.py` | `extract()`, `load_baseline()`, `main()`, and `--emit-baseline` |
| `ci/pytest-baseline.txt` | known-failing test ids, plus the command that produced them |
| `ci/smoke/` | the post-deploy suite. See `ci/smoke/README.md`, which documents it in full |

### The registry

A `Route` carries the resolver pattern verbatim, a requestable path that may hold
`{placeholders}`, the methods CI sends, the profiles it may be called under, and
an expected status (`ci/routes.py:37-55`). `expect` names the status a *working*
route returns, never the status a broken one returns today, which is what lets an
`xfail` flip to XPASS the day a defect is fixed (`ci/routes.py:44-49`).
`__post_init__` normalises `profiles` to a frozenset and `methods` to an
upper-cased tuple at construction, because `"od" in "local,dev,prod"` is a
substring test that passes silently and iterating the string `"GET"` yields three
characters (`ci/routes.py:57-77`). Validation refuses a route with neither profiles nor an
exclusion, and refuses a free-text exclusion reason because this repository is
public (`ci/routes.py:81-89`).

Measured 2026-09-03 by importing the module and counting every entry of the
`REGISTRY` list that begins at `ci/routes.py:204`: 159 entries, 157 of them
`resolver=True`; 26 carry an exclusion; 11 carry an `xfail`; 133 entries name
`local`, 133 name `dev`, 79 name `prod`; and exactly one sets
`prod_allows_non_get`, the `^login` route at `ci/routes.py:219-223`. The
placeholder vocabulary those paths draw on holds 13 names
(`ci/routes.py:162-180`).

`match()` resolves a URL to the most specific declaration — the pattern that
pins the whole path first, then the one spelling out the most literal characters
— which reproduces the ordering Django's own resolver uses when a viewset's
detail route would otherwise swallow its list-level action
(`ci/routes.py:907-921`). `_check_unique_patterns` runs against `REGISTRY` at
import, so a duplicated pattern is an `ImportError` rather than a silently
unreachable second entry (`ci/routes.py:882-899`, `ci/routes.py:924`).

### The gate

`live_patterns()` walks Django's resolver and returns the patterns CI owns:
everything under `nextseek_api/` or `seek/`, plus seven project-level patterns
listed at `ci/gate/live_routes.py:42-50`. The Django admin and every DRF
format-suffix twin are dropped from the denominator entirely rather than declared
(`ci/gate/live_routes.py:31-37`, `ci/gate/live_routes.py:57-63`). A `path()`
route using converter syntax raises `NotImplementedError` instead of being
declared, because `Route.matcher` is a plain regex and would never match it
(`ci/gate/live_routes.py:122-130`). The two tests then diff that set against the
registry in both directions and fail with a paste-ready skeleton
(`ci/gate/test_route_registry.py:29-49`).

### The baseline differ

`extract()` scopes its parse to pytest's "short test summary info" block, because
an anchored grep for `^ERROR` over the whole output also matches Django log
records whose level is literally ERROR (`ci/diff_baseline.py:11-16`,
`ci/diff_baseline.py:41-58`). It compares deduplicated *names*, never totals, and
splits a result line on the ` - ` separator rather than on whitespace, so a
parametrised id containing spaces survives (`ci/diff_baseline.py:32-36`).
`--emit-baseline` prints the run's failing ids in baseline format; using it
rather than an ad-hoc grep is what keeps the two sides on one parser
(`ci/diff_baseline.py:77-80`).

## What runs where

Four callers invoke these lanes and they do not overlap, so "CI passed" means a
different thing depending on which one ran.

**`.github/workflows/ci-pytest.yml`** runs on every push to `dev` or `main` and
on every pull request (`.github/workflows/ci-pytest.yml:10-14`). It runs the
*application's* pytest suite over six named directories, scores that output
against the committed baseline, and finishes with `uv run pytest ci/gate -q`
(`.github/workflows/ci-pytest.yml:62-64`,
`.github/workflows/ci-pytest.yml:68-72`,
`.github/workflows/ci-pytest.yml:80-86`). It never runs `ci/smoke/`. Its own
name is the summary: `pytest (informational)`
(`.github/workflows/ci-pytest.yml:1`).

**`.github/workflows/ci-smoke.yml`** runs by hand only — `workflow_dispatch` is
its single trigger (`.github/workflows/ci-smoke.yml:11-12`) — on a self-hosted
runner labelled `fairdata-dev` (`.github/workflows/ci-smoke.yml:44`). It reads
the box's profile out of `startup/.instance.json` rather than naming one
(`.github/workflows/ci-smoke.yml:55-68`), refuses the write lane outright on a
box declaring `prod` (`.github/workflows/ci-smoke.yml:74-78`), runs
`pytest ci/smoke/` (`.github/workflows/ci-smoke.yml:101-103`) and, only when
asked for, `pytest ci/smoke/ -m write` (`.github/workflows/ci-smoke.yml:105-110`).
The operator rebuilds and this workflow then tests; it never restarts anything
(`.github/workflows/ci-smoke.yml:3-6`).

**`./startup.sh ci`** runs `pytest ci/smoke/` and nothing else. The argv is
built by one pure function holding that single literal
(`startup/ci/runner.py:38-56`), so the gate, the baseline differ and the
application's own suite are all outside this entry point. It sets
`CI_BOX_PROFILE` from the instance state, falling back to `prod`
(`startup/ci/runner.py:62`). Every run also leaves a markdown record under
`startup/ci-reports/`, named for the image it tested, carrying the counts and
every non-passing outcome (`startup/ci/runner.py:write_report`). It is
gitignored: each box records its own runs.

**The rebuild hook** runs that same command with the readiness gate on after a
successful `./startup.sh rebuild` unless `--no-ci` is passed
(`startup/cli.py:632-646`), and skips itself when the restart was deferred,
because the running containers would still carry the previous image
(`startup/cli.py:633-639`).

### What can fail a job, and what is only a report

In `ci-pytest.yml` the pytest step is `continue-on-error: true`
(`.github/workflows/ci-pytest.yml:46`) and the differ always exits 0 by decision
(`ci/diff_baseline.py:8-9`), so `uv run pytest ci/gate -q` is the only step whose
exit code can fail that job, which the comment above it says in as many words
(`.github/workflows/ci-pytest.yml:74-79`). A red pytest lane there is a report
about known-failing tests; an undeclared route is a stop.

The two smoke callers are the other way round. Every test in `ci/smoke/` counts,
and a non-zero run makes `./startup.sh rebuild` exit with the suite's own code
without undoing the rebuild (`startup/cli.py:648-658`).

## Vocabulary

The words a reader meets in a run's output or in a registry entry. Where a term
names something that is declared but not yet built, it says so.

| term | what it means |
|---|---|
| **tier** | How deep a check goes. Six are declared, T0 to T5 (`docs/superpowers/specs/2026-09-01-nextseek-ci-comprehensive-coverage-design.md:244-255`): T0 and T1 are parametrised from the registry and grow with it, T2 upward are hand-written because a browser interaction is not a table row. Only T0 is built; `ci/smoke/README.md:57-58` records that per-route body assertions are T1's job "and are not in this increment" |
| **T0 / reachability** | `ci/smoke/test_reachability.py`: one test per registry route, parametrised at collection, asserting a status, a live gateway and no silent bounce to `/login/` (`ci/smoke/test_reachability.py:1-10`). Deliberately shallow, which is why the hand-written tests exist beside it |
| **flows** | `ci/smoke/test_flows.py`: the browser lane, marked `flow` (`ci/smoke/test_flows.py:19`), which drives the real UI through Playwright and with `--strict-console` fails on uncaught console errors. Nothing in it writes to the database (`ci/smoke/test_flows.py:1-9`) |
| **route registry** | The `REGISTRY` list in `ci/routes.py`. Every application URL declared exactly once; an undeclared route is refused before the request is built (`ci/routes.py:10-13`) |
| **completeness gate** | `ci/gate/`: the two tests that diff Django's live resolver against `REGISTRY` in both directions and fail with a paste-ready skeleton (`ci/gate/test_route_registry.py:29-49`) |
| **profile** | Which box the suite believes it is on: `local`, `dev` or `prod` (`ci/routes.py:22`). Every route names the profiles it may be called under. An absent `CI_BOX_PROFILE` resolves to `prod`, the most restrictive, and `--profile` can only narrow from there (`ci/smoke/conftest.py:142-145`, `ci/smoke/conftest.py:162-170`) |
| **auth level** | Which client calls a route: `anon`, `smoke`, `web` or `write` (`ci/routes.py:43`), pinned to exactly those four (`ci/smoke/test_registry_contents.py:110`). `anon` carries no credentials, `smoke` is Basic-authenticated for `/nextseek_api/*`, `web` holds the session cookie the `/seek/*` views read, and the sweep has no `write` client at all (`ci/smoke/test_reachability.py:102-109`) |
| **write lane** | `ci/smoke/test_write_lane.py`, marked `write` (`ci/smoke/test_write_lane.py:33`) and deselected unless `-m` is passed (`ci/smoke/conftest.py:225-230`). It authenticates as the superuser account, proves the dry-run contracts by default, and puts a real INSERT behind a second opt-in, `CI_WRITE_DESTRUCTIVE=1` (`ci/smoke/test_write_lane.py:1-17`) |
| **xfail / XPASS** | A route broken today carries an `xfail` reason and reports `xfailed`. Because `expect` names the status a *working* route returns, the day the defect is fixed that same entry reports **XPASS**, which is the signal to delete the pin rather than a new failure (`ci/routes.py:44-49`, `ci/smoke/README.md:130-133`) |
| **shape** | A `Route` field naming one key that must exist in the JSON body (`ci/routes.py:50`). Declared and asserted by nothing: a grep for `.shape` across `ci/` finds no reader. It is T1's input, carried over from the body assertions that used to be hand-written (`ci/smoke/test_health.py:15-17`) |
| **pytest baseline** | `ci/pytest-baseline.txt`: which tests were already failing, recorded so a run reports what is *new* rather than what is red. Valid for one exact command and one tree state, and it names that command in its own header (`ci/pytest-baseline.txt:3-11`). See `ci/CLAUDE.md` for the measurement showing it is stale against this branch |

## Running and testing

Four lanes touch this boundary. Three were run on 2026-09-03 from this worktree
and are reported below with their real output; the fourth is named with the
infrastructure it needs.

**The gate.** Run it in a throwaway container over a read-only mount of the
worktree, as its own docstring prescribes at `ci/gate/live_routes.py:11-25`:

```bash
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest ci/gate -q -p no:cacheprovider
```

2026-09-03: **5 passed, 3 warnings in 7.88s** (13.6s wall including container
start). The `mkdir` on the first line is load-bearing; see `ci/CLAUDE.md` for
what happens without it. The `-e LOG_DIR` is belt-and-braces under this settings
module, which already points `LOG_DIR` at a writable temporary directory before
importing the real settings (`dmac/test_settings.py:12-16`); omitting it still
gave 5 passed in 7.74s.

**The no-stack part of the smoke suite.** Needs neither a container nor a
deployed stack, because `ci/routes.py` is importable anywhere:

```bash
CI_BOX_PROFILE=local uv run --no-project --with pytest --with requests pytest \
  ci/smoke/test_registry_unit.py ci/smoke/test_registry_contents.py \
  ci/smoke/test_guard_unit.py ci/smoke/test_profile_unit.py \
  ci/smoke/test_assertions_unit.py ci/smoke/test_readiness_unit.py \
  ci/smoke/test_terminal_unit.py -q
```

2026-09-03, on the host with no Django installed: **114 passed in 0.07s**.

**The rest of the smoke suite** — `test_reachability.py`, `test_health.py`,
`test_flows.py`, `test_write_lane.py` — was `(not run)`. It needs a deployed
stack reachable through its nginx front door, and both CI accounts named in
`~/.config/nextseek/ci.env` having logged in through `/login/` on that box at
least once. On 2026-09-03 this host published no nginx port, so there was no
front door to point `--base-url` at. `./startup.sh ci` is the operator entry
point for it (`startup/README.md:38`).

**The baseline lane.** See `ci/CLAUDE.md` for the state it is in; the run that
established that is recorded there with its numbers.

## Depends on / depended on by

Two directions, two different shapes. Inbound is mostly not imports at all: it is
Django's URL resolver, one container image, and two files read by path, so that
list was derived by reading every import and every path constant in this
boundary's own modules. Outbound is one import plus a command string, derived by
grepping every `.py` file in the tree for `ci.routes`, `ci.gate`, `ci.smoke`,
`from ci import`, `ci/routes`, `ci/gate`, `ci/smoke`, `ci/diff_baseline` and
`pytest-baseline`, then removing this boundary's own 24 files.

**Depends on:**

- Django's URL resolver, imported inside `live_patterns()` and `_walk()` so that
  the module stays importable without it (`ci/gate/live_routes.py:93`,
  `ci/gate/live_routes.py:113`); a module-scope import here would break the smoke
  lane, which has no Django.
- The `nextseek` application image, for the gate lane only: the recipe runs
  `/app/.venv/bin/python` from that image against a mount of this worktree
  (`ci/gate/live_routes.py:17-20`).
- `requests`, in the smoke lane only: imported at `ci/smoke/client.py:11` and
  `ci/smoke/conftest.py:41`, and by nothing under `ci/gate/` or in
  `ci/routes.py:15-20`.
- `~/.config/nextseek/ci.env`, read for the smoke credentials
  (`ci/smoke/conftest.py:50`) and reported by `startup/steps/doctor.py:13`. It is
  load-bearing input, not scratch: move or delete it and every authenticated
  smoke test degrades to a skip.
- `startup/.instance.json`, whose `ci_profile` key decides which routes may be
  called on this box (`startup/ci/runner.py:62`).

**Depended on by:**

- `scripts/dump_routes.py:26-27` takes `live_patterns`, `suggest_path` and
  `REGISTRY`, and is the only file outside this boundary that imports anything
  from it: the grep described above returns six `.py` files, and in the other
  five the match is the literal `ci/smoke/` inside an argv list or a test
  assertion, or a path here named in a comment, never an import statement.
- `startup/ci/runner.py:38-56`, which builds the smoke suite's argv with the
  literal string `ci/smoke/` and subprocesses it rather than importing it, so
  that `./startup.sh` stays installable on a host with no C toolchain
  (`startup/ci/runner.py:1-6`).
- `startup/cli.py:630-644`, the rebuild hook that runs the suite after a
  successful rebuild unless `--no-ci` is passed.
- `.github/workflows/ci-pytest.yml:68-72` runs the differ, and
  `.github/workflows/ci-pytest.yml:80-86` runs the gate as the one step whose
  exit code can fail that job.
- `.github/workflows/ci-smoke.yml:101-103` runs the smoke suite on a self-hosted
  runner in a deliberately isolated environment.
- Not a consumer: `startup/cli.py:40-44` restates `("local", "dev", "prod")` as
  its own constant and says in the comment above it that `startup/` never imports
  `ci/`. It is a deliberate duplicate, not an edge.
- Excluded from this list: `ci/smoke/`'s own modules, and every `README.md`,
  `CLAUDE.md` and `CITATIONS.txt` in a sibling boundary that cites a path here.
  The registry names its two consuming environments and the application is
  neither of them (`ci/routes.py:5-6`); no module under `dmac/`, `seek/`,
  `nextseek_api/`, `chat_nextseek/` or `api_app/` appears at all, because the
  grep above returns six `.py` files outside `ci/` and all six are
  `scripts/dump_routes.py` or under `startup/`.
