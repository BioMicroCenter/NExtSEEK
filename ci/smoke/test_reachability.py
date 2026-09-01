"""T0: every route the active profile permits is reachable and honest.

    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with requests \
      --with playwright pytest ci/smoke/test_reachability.py \
      --base-url http://127.0.0.1:8000 -q -p no:cacheprovider

One test per registry route, parametrised at collection, so the tier grows by
itself as the registry does and one broken route cannot hide the other ninety.
The assertions are deliberately shallow -- a status, a live gateway, and no
silent bounce to the login page. Body shape is T1's job.

Two things here are load-bearing rather than stylistic:

  * The test id is the route's TEMPLATE path, `{sample_id}` and all, never the
    resolved one. Ids are printed, pasted into tickets and stored in CI logs, and
    under the prod profile the resolved value is a real production identifier.
    Failure messages name the template for the same reason, and never a body.
  * Every request goes through a client from `clients`, which holds no `write`
    entry at all. The sweep does not merely decline to call a write-auth route:
    it has nothing to call one with.
"""
from __future__ import annotations

import string
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.routes import REGISTRY
from ci.smoke.assertions import assert_not_bounced, check_gateway
from ci.smoke.conftest import resolve_profile


def _callable_routes(profile: str) -> list:
    """The routes T0 requests under this profile.

    `auth != "write"` is the safety clause, not an optimisation: those routes need
    the superuser account, and the whole sweep is built on never holding it.
    A route with no path or no GET is one the registry declares but does not ask
    CI to call.
    """
    return [
        r for r in REGISTRY
        if profile in r.profiles and r.path and "GET" in r.methods and r.auth != "write"
    ]


def _placeholder_names(path: str) -> list[str]:
    """The {names} a path template needs filled in, in the order they appear."""
    return [field for _, field, _, _ in string.Formatter().parse(path) if field]


def pytest_generate_tests(metafunc):
    """Turn the registry into one test item per route.

    Parametrising here rather than looping inside one test is what makes each
    route report separately: its own id, its own xfail reason, its own skip when
    the environment has no value for a placeholder it needs.

    resolve_profile is called rather than the `profile` fixture because no fixture
    exists yet at collection time. It is memoised on the config, and
    pytest_configure has already resolved it, so this neither re-reads the command
    line nor re-prints the forced-profile banner.
    """
    if "route" not in metafunc.fixturenames:
        return
    profile = resolve_profile(metafunc.config)
    routes = _callable_routes(profile)
    if not routes:
        # Skipped, not raised. A collection error here would be indistinguishable
        # from a broken harness, and the honest report is that this profile has
        # nothing to sweep.
        metafunc.parametrize(
            "route",
            [pytest.param(None, marks=pytest.mark.skip(
                reason=f"no route in the registry is enabled for profile {profile!r}"))],
            ids=["no-enabled-route"],
        )
        return
    metafunc.parametrize(
        "route",
        [
            pytest.param(r, marks=pytest.mark.xfail(reason=r.xfail, strict=False))
            if r.xfail else r
            for r in routes
        ],
        ids=[r.path for r in routes],
    )


@pytest.fixture(scope="session")
def clients(anon, api, web):
    """The three identities T0 sweeps with, and deliberately no fourth.

    `anon` carries no credentials, `api` is Basic-authenticated for
    /nextseek_api/*, and `web` holds the session cookie the /seek/* views read.
    There is no "write" key: see the module docstring.
    """
    return {"anon": anon, "smoke": api, "web": web}


def test_route_is_reachable(route, base_url, discovered, clients):
    template = route.path
    missing = [n for n in _placeholder_names(template) if not discovered.get(n)]
    if missing:
        # Only this route, not the run. An environment without data files still
        # sweeps the other ninety-two.
        pytest.skip(
            "no value for "
            + ", ".join("{" + name + "}" for name in missing)
            + " in this environment"
        )

    path = template.format(**discovered)
    # Accept: */* on every request, whatever the client's own default is. T0
    # asserts reachability, not representation, and two routes -- the swagger and
    # redoc UIs -- answer 406 to a JSON-only Accept because they serve HTML.
    r = clients[route.auth].get(
        base_url + path, timeout=90, allow_redirects=False,
        headers={"Accept": "*/*"},
    )
    check_gateway(r)
    if route.auth != "anon":
        assert_not_bounced(r)
    expected = route.expect if isinstance(route.expect, tuple) else (route.expect,)
    assert r.status_code in expected, (
        f"{template} returned {r.status_code}; the registry expects "
        f"{', '.join(str(code) for code in expected)}"
    )
