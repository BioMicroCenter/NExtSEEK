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
point for it (`startup/README.md:156-192`).

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
  called on this box (`startup/ci/runner.py:54`).

**Depended on by:**

- `scripts/dump_routes.py:26-27` takes `live_patterns`, `suggest_path` and
  `REGISTRY`, and is the only file outside this boundary that imports anything
  from it: the grep described above returns six `.py` files, and in the other
  five the match is the literal `ci/smoke/` inside an argv list or a test
  assertion, or a path here named in a comment, never an import statement.
- `startup/ci/runner.py:35-41`, which builds the smoke suite's argv with the
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
