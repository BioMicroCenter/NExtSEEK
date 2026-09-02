# CI Increment 1: Skeleton and Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every one of the ~157 application routes is declared in a registry that CI enforces, and a client that structurally cannot issue a write under the production profile.

**Architecture:** A dependency-free registry module (`ci/routes.py`) is imported by two lanes that share nothing else: the in-container pytest lane, where a completeness gate diffs it against Django's live URL resolver, and the isolated out-of-container smoke lane, where it drives parametrised reachability tests and backs a `requests.Session` subclass that refuses any URL or method the active profile does not permit. `startup/` gains a thin subprocess shim so a rebuild runs CI without the operator remembering to.

**Tech Stack:** Python 3.13+ stdlib only for `ci/routes.py`; pytest + requests + playwright for the smoke lane; Django 5.2 for the gate; typer for the shim.

**Spec:** `docs/superpowers/specs/2026-09-01-nextseek-ci-comprehensive-coverage-design.md`

## Global Constraints

- `ci/routes.py` MUST import **stdlib only**. It is imported by the smoke lane (which has no Django) and by the pytest lane (which has no requests/playwright). Any third-party import breaks one of them.
- `startup/` MUST NOT gain dependencies. It is pinned to typer, rich, neo4j, orjson, PyMySQL so `./startup.sh` stays bootstrappable on a host with no C toolchain. The shim **subprocesses**, never imports, the smoke suite.
- Never pass `-p no:logging` to pytest. It removes the plugin providing `caplog` and manufactures "fixture 'caplog' not found" errors.
- Always pass explicit test paths and `--continue-on-collection-errors`. A bare `pytest` at the repo root hits 6 collection errors and runs zero tests.
- `exclude` values are **category codes**, never descriptions. This repo is public. Allowed: `EXCLUDE_COST`, `EXCLUDE_EXTERNAL`, `EXCLUDE_UNSAFE_METHOD`, `EXCLUDE_DEAD`, `EXCLUDE_ADMIN`.
- `xfail` uses `strict=False` throughout, so a route that starts working reports XPASS instead of failing the suite.
- Set `PYTHONDONTWRITEBYTECODE=1` for any pytest run. This repo has been bitten by stale `.pyc` giving untrustworthy results.

---

## File Structure

| File | Responsibility |
|---|---|
| `ci/routes.py` | CREATE. `Route` dataclass, `REGISTRY` list, `match()`. Stdlib only. |
| `ci/__init__.py` | CREATE. Empty, makes `ci` importable. |
| `ci/gate/__init__.py` | CREATE. Empty. |
| `ci/gate/test_route_registry.py` | CREATE. The completeness gate. Needs Django. |
| `scripts/dump_routes.py` | CREATE. Walks the resolver, emits registry skeleton entries. |
| `ci/smoke/client.py` | CREATE. `GuardedSession`, `ProfileViolation`. |
| `ci/smoke/conftest.py` | MODIFY. Add `--profile`, `--force-profile`; swap fixtures to `GuardedSession`; guard the browser context. |
| `ci/smoke/test_reachability.py` | CREATE. T0, parametrised from the registry. |
| `ci/smoke/test_health.py` | MODIFY. Drop the routes T0 now covers; keep hand-written contract assertions. |
| `startup/lib/instance.py` | MODIFY. Add `ci_profile: str = ""` to `InstanceState`. |
| `startup/ci/__init__.py` | CREATE. Empty. |
| `startup/ci/runner.py` | CREATE. Profile resolution + subprocess invocation. |
| `startup/cli.py` | MODIFY. `ci` subcommand; `--no-ci` on `rebuild`; hook at end of `rebuild()`. |
| `.github/workflows/ci-pytest.yml` | MODIFY. Add `ci/gate` to the path list; make the gate blocking. |

---

### Task 1: The registry module

**Files:**
- Create: `ci/__init__.py`, `ci/routes.py`
- Test: `ci/smoke/test_registry_unit.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Route` (frozen dataclass), `REGISTRY: list[Route]`, `match(url_path: str) -> Route | None`, `PROFILES = ("local","dev","prod")`, `EXCLUDE_CODES: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `ci/smoke/test_registry_unit.py`:

```python
"""Unit tests for the route registry. No network, no stack, no Django."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from ci.routes import Route, match, EXCLUDE_CODES, REGISTRY


def test_profiles_string_is_normalised_to_a_frozenset():
    r = Route(pattern=r"^x/$", path="/x/", methods=("GET",), profiles="local,dev")
    assert r.profiles == frozenset({"local", "dev"})


def test_substring_cannot_masquerade_as_a_profile():
    """The bug this normalisation exists to prevent: 'od' in 'local,dev,prod' is True."""
    r = Route(pattern=r"^x/$", path="/x/", methods=("GET",), profiles="local,dev,prod")
    assert "od" not in r.profiles
    assert "prod" in r.profiles


def test_empty_profiles_requires_an_exclude_code():
    with pytest.raises(ValueError, match="exclude"):
        Route(pattern=r"^x/$", path="/x/", methods=("GET",), profiles="")


def test_exclude_code_must_be_from_the_allowed_set():
    with pytest.raises(ValueError, match="category code"):
        Route(pattern=r"^x/$", path=None, methods=(), profiles="",
              exclude="a prose reason instead of a category code")


def test_exclude_code_accepted():
    r = Route(pattern=r"^x/$", path=None, methods=(), profiles="",
              exclude="EXCLUDE_UNSAFE_METHOD")
    assert r.exclude in EXCLUDE_CODES


def test_matcher_strips_nested_anchors_from_a_concatenated_pattern():
    """Django patterns concatenate as '^seek/^sample/...$'. The matcher must cope."""
    r = Route(pattern=r"^seek/^sample/id=(?P<id>\d+)/$",
              path="/seek/sample/id={sample_id}/", methods=("GET",), profiles="dev")
    assert r.matches("/seek/sample/id=334598/")
    assert not r.matches("/seek/sample/id=abc/")


def test_match_finds_the_route_for_a_full_url():
    assert match("http://127.0.0.1:8000/nextseek_api/sops/") is not None or REGISTRY == []
```

- [ ] **Step 2: Run test to verify it fails**

Run, from the repo root: `uv run --no-project --with pytest pytest ci/smoke/test_registry_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ci.routes'`

- [ ] **Step 3: Write the implementation**

Create `ci/__init__.py` as an empty file. Create `ci/routes.py`:

```python
"""The CI route registry.

STDLIB ONLY. This module is imported by two environments that share nothing:

  * ci/gate/     runs inside the pytest lane, which has Django but not requests
  * ci/smoke/    runs outside the container, which has requests but not Django

A third-party import here breaks one of them.

Every application route is declared exactly once. A route that is not declared
is refused at request time, which is what makes running against production
defensible: dangerous routes are excluded because nobody opted them in, not
because somebody remembered to list them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from urllib.parse import urlsplit

PROFILES = ("local", "dev", "prod")

EXCLUDE_CODES = frozenset({
    "EXCLUDE_COST",           # calls a paid model
    "EXCLUDE_EXTERNAL",       # needs Luria SSH or another external system
    "EXCLUDE_UNSAFE_METHOD",  # unsafe to call from an automated sweep
    "EXCLUDE_DEAD",           # route cannot function; tracked separately
    "EXCLUDE_ADMIN",          # administrative surface, out of scope for CI
})


@dataclass(frozen=True)
class Route:
    pattern: str                      # verbatim from Django's resolver; the gate diffs this
    path: str | None                  # concrete request path, may contain {placeholders}
    methods: tuple[str, ...]          # what CI will send, not what the route allows
    profiles: frozenset[str]          # empty means never called; then exclude is required
    auth: str = "smoke"               # anon | smoke | web | write
    expect: int | tuple[int, ...] = 200
    shape: str | None = None          # a key that must exist in the JSON body
    xfail: str | None = None          # reason, when the route is broken today
    exclude: str | None = None        # a CATEGORY CODE; see EXCLUDE_CODES
    note: str | None = None

    def __post_init__(self) -> None:
        # Entries are authored as profiles="local,dev" for readability. Normalise to a
        # frozenset so exactly one representation exists at run time. Without this,
        # `profile in route.profiles` is a SUBSTRING test: "od" in "local,dev,prod"
        # is True, and the guard silently passes garbage.
        if isinstance(self.profiles, str):
            object.__setattr__(
                self, "profiles",
                frozenset(p.strip() for p in self.profiles.split(",") if p.strip()),
            )
        unknown = self.profiles - set(PROFILES)
        if unknown:
            raise ValueError(f"unknown profile(s) {sorted(unknown)} in {self.pattern}")
        if not self.profiles and not self.exclude:
            raise ValueError(
                f"{self.pattern}: a route with no profiles must carry an exclude code"
            )
        if self.exclude and self.exclude not in EXCLUDE_CODES:
            raise ValueError(
                f"{self.pattern}: exclude must be a category code from "
                f"{sorted(EXCLUDE_CODES)}, not a description. This repo is public."
            )

    @cached_property
    def matcher(self) -> re.Pattern[str]:
        """A regex matching a request path.

        Django patterns arrive concatenated from nested include()s, so they carry
        interior anchors: '^seek/^sample/id=(?P<id>\\d+)/$'. Strip every ^ and $,
        then anchor once.
        """
        body = re.sub(r"[\^$]", "", self.pattern)
        return re.compile("^/" + body.lstrip("/") + "$")

    def matches(self, url_path: str) -> bool:
        return bool(self.matcher.match(url_path))


REGISTRY: list[Route] = []


def match(url: str) -> Route | None:
    """Return the Route for a URL or path, or None when nothing is declared."""
    url_path = urlsplit(url).path or "/"
    for route in REGISTRY:
        if route.matches(url_path):
            return route
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-project --with pytest pytest ci/smoke/test_registry_unit.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add ci/__init__.py ci/routes.py ci/smoke/test_registry_unit.py
git commit -m "feat(ci): route registry with fail-closed profile normalisation"
```

---

### Task 2: The route dumper and the completeness gate

**Files:**
- Create: `scripts/dump_routes.py`, `ci/gate/__init__.py`, `ci/gate/test_route_registry.py`
- Modify: `.github/workflows/ci-pytest.yml`

**Interfaces:**
- Consumes: `ci.routes.REGISTRY`, `ci.routes.Route`.
- Produces: `scripts/dump_routes.py::live_patterns() -> set[str]`, importable by the gate.

- [ ] **Step 1: Write the failing test**

Create `ci/gate/__init__.py` empty. Create `ci/gate/test_route_registry.py`:

```python
"""The completeness gate. Runs in the pytest lane, where Django is importable.

This is the one test in an otherwise informational job that BLOCKS. It is
deterministic and new, so it carries none of the "red on run one" risk that
made the rest of that job informational.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.routes import REGISTRY
from scripts.dump_routes import live_patterns

SUGGESTION = '''    Route(pattern=r"{pattern}",
          path="{path}",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=200)'''


def _suggest(pattern: str) -> str:
    import re
    path = "/" + re.sub(r"[\\^$]", "", pattern).lstrip("/")
    return SUGGESTION.format(pattern=pattern, path=path)


def test_every_route_is_registered():
    live = live_patterns()
    declared = {r.pattern for r in REGISTRY}
    missing = sorted(live - declared)
    assert not missing, (
        f"\\n\\n  {len(missing)} route(s) are not declared in ci/routes.py:\\n\\n"
        + "\\n".join(f"    {p}" for p in missing)
        + "\\n\\n  Add each one, or declare it excluded with a category code:\\n\\n"
        + "\\n\\n".join(_suggest(p) for p in missing[:3])
        + "\\n"
    )


def test_no_stale_registry_entries():
    live = live_patterns()
    declared = {r.pattern for r in REGISTRY}
    stale = sorted(declared - live)
    assert not stale, (
        f"\\n\\n  {len(stale)} registry entr(ies) no longer match any route:\\n\\n"
        + "\\n".join(f"    {p}" for p in stale)
        + "\\n\\n  The route was renamed or removed. Update or delete the entry.\\n"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  nextseek sh -c 'cd /app && uv run pytest ci/gate -q'
```
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.dump_routes'`

- [ ] **Step 3: Write the implementation**

Create `scripts/dump_routes.py`:

```python
#!/usr/bin/env python
"""Walk Django's URL resolver.

Two uses:
  * imported by ci/gate/test_route_registry.py to diff against the registry
  * run directly to print registry skeleton entries for undeclared routes

Requires Django. Run inside the container or in the pytest lane.
"""
from __future__ import annotations

import re

# Routes CI does not own. Excluded from the denominator entirely rather than
# declared, because they are third-party surface: Django admin, Mezzanine's CMS
# catch-all, and the DRF format-suffix duplicates every router generates.
IGNORE_PREFIXES = ("admin/", "^admin/")


def _is_format_suffix(pattern: str) -> bool:
    return "(?P<format>" in pattern


def _walk(resolver, prefix: str = ""):
    for entry in resolver.url_patterns:
        pattern = prefix + str(entry.pattern)
        if hasattr(entry, "url_patterns"):
            yield from _walk(entry, pattern)
        else:
            yield pattern


def live_patterns() -> set[str]:
    """Every application URL pattern CI is responsible for declaring."""
    from django.urls import get_resolver

    out: set[str] = set()
    for pattern in _walk(get_resolver()):
        if pattern.startswith(IGNORE_PREFIXES):
            continue
        if _is_format_suffix(pattern):
            continue
        if not pattern.startswith(("nextseek_api/", "^nextseek_api/", "seek/", "^seek/")):
            # Project-level routes (login, media, home) are declared explicitly by
            # exact pattern in the registry; everything else at the root is Mezzanine.
            if pattern not in _PROJECT_LEVEL:
                continue
        out.add(pattern)
    return out


# Project-level routes CI owns. Anything else at the URL root belongs to Mezzanine.
_PROJECT_LEVEL = {
    "^login",
    "^logout",
}


def main() -> int:
    import django
    django.setup()
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ci.routes import REGISTRY

    missing = sorted(live_patterns() - {r.pattern for r in REGISTRY})
    for pattern in missing:
        path = "/" + re.sub(r"[\^$]", "", pattern).lstrip("/")
        print(f'    Route(pattern=r"{pattern}",')
        print(f'          path="{path}",')
        print( '          methods=("GET",), profiles="local,dev", auth="smoke", expect=200),')
    print(f"\n# {len(missing)} undeclared route(s)", file=__import__("sys").stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add an empty `scripts/__init__.py` if one does not exist, so `from scripts.dump_routes import ...` resolves.

- [ ] **Step 4: Run test to verify it fails for the RIGHT reason**

Run the command from Step 2 again.
Expected: FAIL listing ~157 undeclared routes, with three suggested `Route(...)` blocks. This is the gate working. Task 3 populates the registry.

- [ ] **Step 5: Make the gate blocking in job 1**

In `.github/workflows/ci-pytest.yml`, add a step AFTER the existing "Run the no-stack lanes" step and BEFORE "Diff against the baseline":

```yaml
      # The one blocking check in this otherwise informational job. Deterministic
      # and new, so it carries none of the "red on run one" risk that made the
      # rest of the job informational.
      - name: Route registry completeness gate
        env:
          DJANGO_SETTINGS_MODULE: dmac.test_settings
          PYTHONDONTWRITEBYTECODE: "1"
        run: uv run pytest ci/gate -q
```

Do NOT add `ci/gate` to the `continue-on-error` pytest step: it must be its own step so its exit code is not swallowed. Do NOT add bare `ci` to any path list, or `ci/smoke/` gets collected in an environment with no requests or playwright.

- [ ] **Step 6: Commit**

```bash
git add scripts/dump_routes.py scripts/__init__.py ci/gate/ .github/workflows/ci-pytest.yml
git commit -m "feat(ci): completeness gate diffing the registry against the URL resolver"
```

---

### Task 3: Populate the registry

**Files:**
- Modify: `ci/routes.py` (the `REGISTRY` list)

**Interfaces:**
- Consumes: `scripts/dump_routes.py` output.
- Produces: a populated `REGISTRY` covering every live pattern.

- [ ] **Step 1: Generate the skeleton**

```bash
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run python scripts/dump_routes.py' > /tmp/skeleton.txt
wc -l /tmp/skeleton.txt
```

Expected: roughly 157 `Route(` blocks.

- [ ] **Step 2: Paste the skeleton into `REGISTRY` and classify every entry**

Replace `REGISTRY: list[Route] = []` with the generated list, then edit each entry. The generator defaults everything to `profiles="local,dev"`, `auth="smoke"`, `expect=200`, which is wrong for many. Apply these rules:

| condition | edit |
|---|---|
| read-only `nextseek_api` route | `profiles="local,dev,prod"`, add `shape=` where the body has a stable envelope key |
| a `/seek/` page | `auth="web"` — Basic does not work for `seek` pages, they read `request.session['username']` |
| an anonymous-safe page (`/seek/help/`) | `auth="anon"` |
| a write method | `profiles="local,dev"`, `auth="write"` if superuser-gated |
| calls a model | `profiles=""`, `exclude="EXCLUDE_COST"` |
| needs Luria SSH | `profiles=""`, `exclude="EXCLUDE_EXTERNAL"` |
| mutates on GET | `profiles=""`, `exclude="EXCLUDE_UNSAFE_METHOD"` |
| cannot function | `profiles=""`, `exclude="EXCLUDE_DEAD"` |
| broken today but reachable | keep profiles, add `xfail="<cause>"` |

Known entries to get right, verified on 2026-09-01:

```python
    Route(pattern=r"^nextseek_api/^^schema/$", path="/nextseek_api/schema/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          note="drf-spectacular walks every annotated endpoint; validates 67 paths at once"),
    Route(pattern=r"^seek/^help/$", path="/seek/help/",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=200),
    Route(pattern=r"^seek/^url/(?P<url>[\w-]+)/$", path="/seek/url/smoke/",
          methods=("GET",), profiles="local,dev", auth="web", expect=500,
          xfail="NameError: getPageRequests is not defined at seek/views.py:113"),
    Route(pattern=r"^seek/^remote/$", path="/seek/remote/",
          methods=("GET",), profiles="local,dev", auth="web", expect=500,
          xfail="NameError: samples is not defined at seek/views.py:499"),
    Route(pattern=r"^nextseek_api/^^entity_tree/nodes/$", path="/nextseek_api/entity_tree/nodes/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          xfail="34 sample types have no attribute definitions; endpoint returns an "
                "application-level 502 rather than emit empty metadata_fields"),
```

- [ ] **Step 3: Run the gate to verify the registry is complete**

Run:
```bash
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  nextseek sh -c 'cd /app && uv run pytest ci/gate -q'
```
Expected: PASS, 2 passed

- [ ] **Step 4: Run the registry unit tests**

Run: `uv run --no-project --with pytest pytest ci/smoke/test_registry_unit.py -q`
Expected: PASS. The `__post_init__` validation catches any entry with empty profiles and no exclude code, or an exclude description instead of a code.

- [ ] **Step 5: Commit**

```bash
git add ci/routes.py
git commit -m "feat(ci): declare all application routes in the registry"
```

---

### Task 4: The guarded HTTP client

**Files:**
- Create: `ci/smoke/client.py`
- Test: `ci/smoke/test_guard_unit.py`

**Interfaces:**
- Consumes: `ci.routes.match`, `ci.routes.Route`.
- Produces: `GuardedSession(profile: str, **kw)`, `ProfileViolation(Exception)`.

- [ ] **Step 1: Write the failing test**

Create `ci/smoke/test_guard_unit.py`:

```python
"""The guard must refuse before sending. These tests make no network calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from ci import routes
from ci.routes import Route
from ci.smoke.client import GuardedSession, ProfileViolation


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(routes, "REGISTRY", [
        Route(pattern=r"^nextseek_api/^^sops/$", path="/nextseek_api/sops/",
              methods=("GET", "POST"), profiles="local,dev,prod"),
        Route(pattern=r"^nextseek_api/^^samples/$", path="/nextseek_api/samples/",
              methods=("POST",), profiles="local,dev"),
        Route(pattern=r"^seek/^admin/clades/syncSampleTypes/$",
              path=None, methods=(), profiles="", exclude="EXCLUDE_UNSAFE_METHOD"),
    ])


def test_unregistered_url_is_refused():
    s = GuardedSession(profile="dev")
    with pytest.raises(ProfileViolation, match="unregistered"):
        s.get("http://h/nextseek_api/not_declared/")


def test_excluded_route_is_refused_even_on_local():
    s = GuardedSession(profile="local")
    with pytest.raises(ProfileViolation, match="not enabled"):
        s.get("http://h/seek/admin/clades/syncSampleTypes/")


def test_route_not_enabled_for_this_profile_is_refused():
    s = GuardedSession(profile="prod")
    with pytest.raises(ProfileViolation, match="not enabled"):
        s.post("http://h/nextseek_api/samples/")


def test_prod_refuses_every_non_get_even_on_a_permitted_route():
    s = GuardedSession(profile="prod")
    with pytest.raises(ProfileViolation, match="refused under the prod profile"):
        s.post("http://h/nextseek_api/sops/")


def test_substring_profile_cannot_pass_the_guard():
    """Regression guard for the frozenset normalisation."""
    s = GuardedSession(profile="od")
    with pytest.raises(ProfileViolation):
        s.get("http://h/nextseek_api/sops/")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-project --with pytest --with requests pytest ci/smoke/test_guard_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ci.smoke.client'`

- [ ] **Step 3: Write the implementation**

Create `ci/smoke/client.py`:

```python
"""The profile guard.

Enforcement lives here, in the client, and not in the tests. A rule a test
author has to remember is a rule that eventually gets forgotten, and the
failure mode of forgetting this one is a write against production.
"""
from __future__ import annotations

import requests

from ci import routes


class ProfileViolation(RuntimeError):
    """Raised BEFORE a request leaves the process."""


class GuardedSession(requests.Session):
    def __init__(self, profile: str, **kwargs) -> None:
        super().__init__()
        self.profile = profile
        for k, v in kwargs.items():
            setattr(self, k, v)

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        route = routes.match(url)
        if route is None:
            raise ProfileViolation(
                f"unregistered URL: {url}\n"
                f"Declare it in ci/routes.py, or the guard cannot know whether it is safe."
            )
        if self.profile not in route.profiles:
            reason = f" ({route.exclude})" if route.exclude else ""
            raise ProfileViolation(
                f"{url} is not enabled for profile {self.profile!r}{reason}"
            )
        if self.profile == "prod" and method.upper() != "GET":
            raise ProfileViolation(
                f"{method.upper()} refused under the prod profile: {url}"
            )
        return super().request(method, url, *args, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-project --with pytest --with requests pytest ci/smoke/test_guard_unit.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add ci/smoke/client.py ci/smoke/test_guard_unit.py
git commit -m "feat(ci): profile guard that refuses before a request is sent"
```

---

### Task 5: Wire the profile through conftest and guard the browser

**Files:**
- Modify: `ci/smoke/conftest.py`

**Interfaces:**
- Consumes: `GuardedSession`, `ProfileViolation`.
- Produces: fixtures `profile: str`, `api: GuardedSession`, `web: GuardedSession`, `page` (browser-guarded).

- [ ] **Step 1: Add the profile options**

In `ci/smoke/conftest.py`, inside `pytest_addoption`, add:

```python
    g.addoption("--profile", default=None,
                help="Narrow the profile below what the box declares. Cannot widen.")
    g.addoption("--force-profile", default=None,
                help="Widen the profile above what the box declares. Requires "
                     "CI_FORCE_PROFILE_CONFIRM=yes. Never use in a workflow file.")
```

And at the top of the file, after the existing imports:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.smoke.client import GuardedSession
```

- [ ] **Step 2: Add the profile fixture**

Add to `ci/smoke/conftest.py`:

```python
_PROFILE_RANK = {"prod": 0, "dev": 1, "local": 2}


@pytest.fixture(scope="session")
def profile(pytestconfig) -> str:
    """Resolve the active profile.

    The box declares a default; the command line may only narrow it. Widening
    needs --force-profile AND an environment acknowledgement, so it cannot be
    reached by a typo or by copying a line out of a workflow file.
    """
    declared = os.environ.get("CI_BOX_PROFILE", "prod")   # absent means prod: fail closed
    forced = pytestconfig.getoption("--force-profile")
    if forced:
        if os.environ.get("CI_FORCE_PROFILE_CONFIRM") != "yes":
            pytest.exit(
                f"--force-profile {forced} needs CI_FORCE_PROFILE_CONFIRM=yes. "
                f"The box declares {declared!r}.", returncode=2)
        print(f"\n*** FORCED PROFILE {forced!r} on a box declaring {declared!r} ***\n")
        return forced
    asked = pytestconfig.getoption("--profile")
    if not asked:
        return declared
    if _PROFILE_RANK[asked] > _PROFILE_RANK[declared]:
        pytest.exit(
            f"--profile {asked!r} would widen past the box's {declared!r}. "
            f"Use --force-profile if that is deliberate.", returncode=2)
    return asked
```

- [ ] **Step 3: Swap the clients to GuardedSession**

Replace the bodies of the `api` and `web` fixtures so both construct `GuardedSession(profile=profile)` instead of `requests.Session()`, adding `profile` to each fixture's parameters. Keep everything else in those fixtures unchanged: `api` still sets `.auth` and never touches `/login/`; `web` still performs the real form POST and asserts the 302.

- [ ] **Step 4: Guard the browser context**

In the `page` fixture, immediately after `p = ctx.new_page()`, add:

```python
    if profile == "prod":
        # Same rule as GuardedSession, at the browser layer. A page that tries to
        # POST under the prod profile gets an aborted request, not a live one.
        ctx.route("**/*", lambda route: (
            route.abort() if route.request.method != "GET" else route.continue_()
        ))
```

and add `profile` to the `page` fixture's parameters.

- [ ] **Step 5: Verify the existing suite still passes**

Run:
```bash
CI_BOX_PROFILE=local CI_SMOKE_USER="$CI_SMOKE_USER" CI_SMOKE_PASS="$CI_SMOKE_PASS" \
uv run --no-project --with pytest --with requests --with playwright \
  pytest ci/smoke/ --base-url http://127.0.0.1:8000 -q
```
Expected: the same 39 passed / 5 skipped / 2 xfailed as before, now routed through the guard.

- [ ] **Step 6: Verify the guard actually refuses on prod**

Run:
```bash
CI_BOX_PROFILE=prod CI_SMOKE_USER="$CI_SMOKE_USER" CI_SMOKE_PASS="$CI_SMOKE_PASS" \
uv run --no-project --with pytest --with requests \
  pytest ci/smoke/test_health.py --base-url http://127.0.0.1:8000 -q
```
Expected: tests hitting routes not marked `prod` fail with `ProfileViolation`, proving the guard is live rather than decorative.

- [ ] **Step 7: Commit**

```bash
git add ci/smoke/conftest.py
git commit -m "feat(ci): box-declared profile, narrowing-only, guarded browser context"
```

---

### Task 6: T0 reachability across every route

**Files:**
- Create: `ci/smoke/test_reachability.py`

**Interfaces:**
- Consumes: `REGISTRY`, `api`, `web`, `profile`, and the existing `check_gateway` helper.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Move `check_gateway` somewhere both files can use it**

Cut `check_gateway` and `assert_not_bounced` out of `ci/smoke/test_health.py` into a new `ci/smoke/assertions.py`, and import them back into `test_health.py`. No behaviour change.

- [ ] **Step 2: Write the reachability test**

Create `ci/smoke/test_reachability.py`:

```python
"""T0: every route the active profile permits is reachable and honest.

Parametrised from the registry, so it grows automatically as the registry does.
Assertions are deliberately shallow: status, not a dead gateway, not a silent
bounce to the login page. Body shape is T1's job.
"""
import pytest

from ci.routes import REGISTRY
from ci.smoke.assertions import check_gateway, assert_not_bounced


def _callable_routes(profile):
    return [r for r in REGISTRY if profile in r.profiles and r.path and "GET" in r.methods]


def _ids(routes):
    return [r.path for r in routes]


@pytest.fixture(scope="session")
def clients(api, web):
    return {"smoke": api, "write": api, "web": web, "anon": None}


def test_reachability(request, base_url, profile, clients, discovered):
    """One test, subtests per route, so a single failure does not hide the rest."""
    routes = _callable_routes(profile)
    assert routes, f"no routes enabled for profile {profile!r}"
    failures = []
    for route in routes:
        path = route.path.format(**discovered)
        client = clients[route.auth]
        try:
            r = (client or __import__("requests")).get(
                base_url + path, timeout=90, allow_redirects=False)
            check_gateway(r)
            if route.auth != "anon":
                assert_not_bounced(r)
            expected = route.expect if isinstance(route.expect, tuple) else (route.expect,)
            if r.status_code not in expected:
                raise AssertionError(f"{r.status_code}, expected {expected}")
        except Exception as exc:
            if route.xfail:
                continue
            failures.append(f"{path}: {exc}")
    assert not failures, "\n  " + "\n  ".join(failures)
```

- [ ] **Step 3: Add the `discovered` fixture**

Add to `ci/smoke/conftest.py`:

```python
@pytest.fixture(scope="session")
def discovered(web, base_url) -> dict[str, str]:
    """Real values for the registry's {placeholders}, found at run time.

    Ids are deployment-specific: the seed and production disagree about all of
    them, so nothing here may be hard-coded.
    """
    r = web.get(f"{base_url}/seek/searchAdvanced/", timeout=180, params={
        "sampletype_id": "", "attribute": "none", "filter_logic": "AND",
        "filter_searchValue": "", "filter_searchText": "Uterus",
        "filter_matchType": "PARTIAL"})
    rows = r.json().get("rows") or []
    if not rows:
        pytest.skip("no samples in this environment to build paths from")
    import re
    return {
        "sample_id": str(rows[0]["id"]),
        "sample_uid": re.sub(r"<[^>]+>", "", str(rows[0]["uid"])).strip(),
        "project_id": "1",
        "sample_type_id": str(rows[0]["sample_type_id"]),
    }
```

- [ ] **Step 4: Run it**

Run:
```bash
CI_BOX_PROFILE=local CI_SMOKE_USER="$CI_SMOKE_USER" CI_SMOKE_PASS="$CI_SMOKE_PASS" \
uv run --no-project --with pytest --with requests --with playwright \
  pytest ci/smoke/test_reachability.py --base-url http://127.0.0.1:8000 -q
```
Expected: PASS. If routes fail, that is real information: either the registry's `expect` is wrong for this environment, or the route is genuinely broken and needs an `xfail`.

- [ ] **Step 5: Commit**

```bash
git add ci/smoke/test_reachability.py ci/smoke/assertions.py ci/smoke/test_health.py ci/smoke/conftest.py
git commit -m "feat(ci): T0 reachability parametrised from the registry"
```

---

### Task 7: The startup shim and the rebuild hook

**Files:**
- Modify: `startup/lib/instance.py:10-20`
- Create: `startup/ci/__init__.py`, `startup/ci/runner.py`
- Modify: `startup/cli.py:474` (add `--no-ci`), `startup/cli.py:586` (add the hook)

**Interfaces:**
- Consumes: `InstanceState`.
- Produces: `startup.ci.runner.run_ci(repo_root, state, wait_ready, profile, force_profile) -> int`.

- [ ] **Step 1: Add `ci_profile` to InstanceState**

In `startup/lib/instance.py`, add to the `InstanceState` dataclass immediately after `seek_public_url`:

```python
    # Which CI profile this box permits. ABSENT MEANS "prod": a machine nobody has
    # configured gets the most restrictive profile, never the least. Defaulted so
    # .instance.json files written before this field still load.
    ci_profile: str = ""
```

- [ ] **Step 2: Write the runner**

Create `startup/ci/__init__.py` empty. Create `startup/ci/runner.py`:

```python
"""Invoke the CI smoke suite.

SUBPROCESSES, never imports. startup/ is pinned to typer, rich, neo4j, orjson and
PyMySQL so ./startup.sh stays bootstrappable on a host with no C toolchain;
importing the suite would drag requests and playwright into it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from startup.lib.instance import InstanceState


def run_ci(repo_root: Path, state: InstanceState, *, wait_ready: bool,
           profile: str | None = None, force_profile: str | None = None) -> int:
    box_profile = state.ci_profile or "prod"      # fail closed
    port = state.ports.get("nextseek", 8000)
    cmd = [
        "uv", "run", "--no-project",
        "--with", "pytest", "--with", "requests", "--with", "playwright",
        "pytest", "ci/smoke/",
        "--base-url", f"http://127.0.0.1:{port}",
    ]
    if wait_ready:
        cmd.append("--wait-ready")
    if profile:
        cmd += ["--profile", profile]
    if force_profile:
        cmd += ["--force-profile", force_profile]
    env = {"CI_BOX_PROFILE": box_profile}
    return subprocess.run(cmd, cwd=repo_root, env={**__import__("os").environ, **env}).returncode
```

- [ ] **Step 3: Add the `ci` subcommand**

In `startup/cli.py`, after the `rebuild` function (which ends at line 586), add:

```python
@app.command()
def ci(
    instance: str | None = typer.Option(None, "--instance"),
    wait_ready: bool = typer.Option(False, "--wait-ready",
                                    help="Apply the readiness floor first. Use after a rebuild."),
    profile: str | None = typer.Option(None, "--profile",
                                       help="Narrow the profile. Cannot widen."),
    force_profile: str | None = typer.Option(None, "--force-profile",
                                             help="Widen past what the box declares. Deliberate only."),
) -> None:
    """Run the CI smoke suite against this instance's running stack."""
    from startup.ci import runner

    state = _require_instance(REPO_ROOT, instance)
    rc = runner.run_ci(REPO_ROOT, state, wait_ready=wait_ready,
                       profile=profile, force_profile=force_profile)
    if rc != 0:
        ui.fail(f"CI failed (exit {rc}). See DEPLOYMENT.md for the rollback procedure.")
        raise typer.Exit(code=rc)
    ui.ok("CI passed")
```

Use whatever helper the neighbouring commands use to load instance state; `rebuild` does it around line 480. Match it rather than inventing `_require_instance` if that name does not exist.

- [ ] **Step 4: Add `--no-ci` to rebuild and the hook**

In the `rebuild` signature (starting `startup/cli.py:474`), add:

```python
    run_ci_after: bool = typer.Option(
        True, "--ci/--no-ci",
        help="Run the CI smoke suite after the rebuild (default: enabled)."),
```

At the very end of `rebuild()`, after the `registry_push` block that ends at line 586:

```python
    if run_ci_after:
        from startup.ci import runner

        ui.info("running CI after rebuild (--no-ci to skip)")
        rc = runner.run_ci(REPO_ROOT, state, wait_ready=True)
        if rc != 0:
            # The rebuild already happened and CI does not undo it. Report and exit
            # non-zero; never auto-roll-back, which is a larger and more dangerous
            # action than the one it would be reacting to.
            ui.fail(f"CI failed after rebuild (exit {rc}). "
                    f"See DEPLOYMENT.md for the rollback procedure.")
            raise typer.Exit(code=rc)
        ui.ok("CI passed")
```

- [ ] **Step 5: Test the shim without a rebuild**

```bash
python3 -c "
import json,pathlib
p=pathlib.Path('startup/.instance.json')
d=json.loads(p.read_text()); d['ci_profile']='local'; p.write_text(json.dumps(d,indent=2))
print('ci_profile set to local')"
CI_SMOKE_USER="$CI_SMOKE_USER" CI_SMOKE_PASS="$CI_SMOKE_PASS" ./startup.sh ci
```
Expected: the suite runs and passes, with no `--wait-ready` delay.

- [ ] **Step 6: Test that the shim refuses to widen**

```bash
python3 -c "
import json,pathlib
p=pathlib.Path('startup/.instance.json')
d=json.loads(p.read_text()); d['ci_profile']='prod'; p.write_text(json.dumps(d,indent=2))"
CI_SMOKE_USER="$CI_SMOKE_USER" CI_SMOKE_PASS="$CI_SMOKE_PASS" ./startup.sh ci --profile local
```
Expected: exits non-zero with "would widen past the box's 'prod'". Then restore `ci_profile` to `local`.

- [ ] **Step 7: Commit**

```bash
git add startup/lib/instance.py startup/ci/ startup/cli.py
git commit -m "feat(startup): ci subcommand, box-declared profile, rebuild hook"
```

---

### Task 8: Migrate the existing tests onto the registry

**Files:**
- Modify: `ci/smoke/test_health.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: nothing.

- [ ] **Step 1: Delete what T0 now covers**

Remove `API_SWEEP`, `test_api_endpoint_is_healthy`, `SEEK_PAGES`, `test_seek_page_renders_for_a_logged_in_user`, `BOUNCING_PAGES`, `test_seek_page_bounces_an_anonymous_visitor`, and `PUBLIC` / `test_public_url_is_served`. Those routes are now registry entries exercised by `test_reachability`.

Keep every test that asserts something a table row cannot express: `test_api_root_advertises_exactly_the_expected_viewsets`, `test_openapi_schema_generates`, `test_identity_probe_responds`, `test_seek_identity_matches_the_authenticated_caller`, `test_ci_account_is_not_a_superuser`, `test_neo4j_is_answering`, `test_edge_attributes_are_enriched`, `test_entity_tree_nodes`, `test_assistant_denies_anonymous_visitors_at_status_200`, `test_login_page_issues_a_csrf_token`.

- [ ] **Step 2: Run the whole suite**

```bash
CI_BOX_PROFILE=local CI_SMOKE_USER="$CI_SMOKE_USER" CI_SMOKE_PASS="$CI_SMOKE_PASS" \
uv run --no-project --with pytest --with requests --with playwright \
  pytest ci/smoke/ --base-url http://127.0.0.1:8000 -q
```
Expected: PASS. Total count will differ from the pre-migration 39 because reachability is now one test with many routes inside it rather than many parametrised tests; what must not change is that the four flows, the write lane skips and the two xfails are all still present.

- [ ] **Step 3: Confirm coverage went up**

```bash
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run python scripts/dump_routes.py' | tail -1
```
Expected: `# 0 undeclared route(s)`

- [ ] **Step 4: Update the smoke README**

Add a "Profiles" section to `ci/smoke/README.md` documenting `CI_BOX_PROFILE`, the narrowing rule, `--force-profile` plus `CI_FORCE_PROFILE_CONFIRM=yes`, and that adding a route means adding one line to `ci/routes.py`.

- [ ] **Step 5: Commit**

```bash
git add ci/smoke/test_health.py ci/smoke/README.md
git commit -m "refactor(ci): migrate hand-written sweeps onto the registry"
```

---

## Self-Review

**Spec coverage.** §4 registry → Task 1, 3. §5 profiles and enforcement → Task 4, 5. §5a shim, box-declared profile, narrowing, rebuild hook → Task 7. §6 T0 → Task 6. §8 completeness gate in job 1, blocking, with a helpful failure message → Task 2. §9 xfail policy → Task 3. §10 category codes → Task 1 (validated in `__post_init__`) and Task 3. §13 migration → Task 8.

**Not in this increment, by design:** §7 production output redaction ships with increment 2, because nothing runs against production until T1 exists. Increment 1's `prod` profile is enforced but untested against a real production box.

**Type consistency.** `Route.profiles` is authored as `str` and stored as `frozenset[str]` via `__post_init__`; every consumer (`GuardedSession.request`, `_callable_routes`) reads the frozenset. `match()` takes a full URL and splits it; `Route.matches()` takes a path. `run_ci` returns `int` and both call sites check it against 0.

**One known gap.** `_PROJECT_LEVEL` in `scripts/dump_routes.py` is a stub containing only login and logout. Task 3 will reveal whether any other root-level route needs declaring; if the gate reports project-level patterns not in that set, extend it rather than widening the `startswith` filter, which would pull in all 28 Mezzanine routes.
