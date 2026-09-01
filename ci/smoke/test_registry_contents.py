"""Safety rules asserted against the REAL registry. No network, no stack, no Django.

test_registry_unit.py proves the Route dataclass enforces its own invariants on
a synthetic entry. This module proves the invariants hold of every entry that is
actually declared, so a future edit that breaks one is a red test rather than a
silent widening of what CI is allowed to do to production.

Each assertion below corresponds to a rule stated in
docs/superpowers/specs/2026-09-01-nextseek-ci-comprehensive-coverage-design.md.
They are here so that relaxing a rule takes a deliberate deletion.

The last three tests are about the registry's CONSUMERS rather than its contents,
and they are here because they are the same claim from the other end: a rule the
registry states is only worth as much as the code that reads it. They stay pure --
no network, no stack, no browser -- so they run in the same no-dependency lane.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.gate.live_routes import suggest_path
from ci.routes import PLACEHOLDERS, PROFILES, REGISTRY, _check_unique_patterns, match
from ci.smoke.conftest import DISCOVERED_KEYS, _guard_context
from ci.smoke.test_reachability import _callable_routes

# Routes Django's resolver reports and CI therefore owns, measured on
# 2026-09-01 by scripts/dump_routes.py against docker/dev. When the application
# gains or loses a route this number moves, and the COMPLETENESS GATE
# (ci/gate/test_route_registry.py) is the authority on what the right number is:
# it diffs the registry against the live resolver. This constant only stops the
# registry drifting silently between gate runs, which happen in a different
# environment.
OWNED_ROUTE_COUNT = 151

# URL paths CI requests that Django's resolver does not report: an nginx-served
# static asset and the Django admin login page.
NON_RESOLVER_COUNT = 2

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _enabled(route):
    return bool(route.profiles)


def test_no_pattern_is_declared_twice():
    """A repeated pattern makes the second entry's rules unreachable through match()."""
    _check_unique_patterns(REGISTRY)


def test_exactly_one_route_may_send_a_non_get_under_prod():
    """Authenticating is a precondition of a read-only sweep. Nothing else qualifies."""
    allowed = [r for r in REGISTRY if r.prod_allows_non_get]
    assert [r.pattern for r in allowed] == [r"^login"], (
        "prod_allows_non_get is the single hole in the read-only guarantee. "
        f"Found: {[r.pattern for r in allowed]}"
    )


def test_no_prod_route_declares_a_write_method():
    """`methods` is what CI sends. Naming a write method on a prod-enabled route
    says CI will send it against production, whatever the client-side guard does."""
    offenders = [
        (r.pattern, sorted(set(r.methods) - {"GET"}))
        for r in REGISTRY
        if "prod" in r.profiles
        and set(r.methods) - {"GET"}
        and not r.prod_allows_non_get
    ]
    assert not offenders, f"non-GET methods declared on prod-enabled routes: {offenders}"


def test_the_seek_admin_surface_never_reaches_prod_or_a_weaker_account():
    """The sweep never runs against /seek/admin/ and never holds the account that
    could reach it.

    Keyed off the pattern as well as the path, so an excluded entry -- which
    carries no path at all -- is still covered if somebody later gives it one.
    """
    for route in REGISTRY:
        under_admin = (
            route.pattern.startswith(r"^seek/^admin/")
            or (route.path or "").startswith("/seek/admin/")
        )
        if not under_admin:
            continue
        assert route.exclude or route.auth == "write", (
            f"{route.pattern} is under /seek/admin/ with auth={route.auth!r}"
        )
        assert "prod" not in route.profiles, (
            f"{route.pattern} is under /seek/admin/ and enabled for prod"
        )


def test_no_prod_route_uses_the_superuser_account():
    """The production sweep never holds superuser rights, so a prod-enabled route
    whose auth is `write` is a route the sweep can only fail on -- or, worse, one
    somebody later gives the credentials to."""
    offenders = [r.pattern for r in REGISTRY if "prod" in r.profiles and r.auth == "write"]
    assert not offenders, f"write-auth routes enabled for prod: {offenders}"


AUTH_VOCABULARY = {"anon", "smoke", "web", "write"}


def test_every_route_declares_an_auth_the_suite_can_supply():
    """`auth` names which client calls the route, and T0 looks it up by name.

    A typo or a fifth value has no client behind it, so the route would either
    raise from a dict index in the middle of the sweep or, worse, be quietly
    dropped by whichever consumer looked it up with .get(). Four values, pinned.
    """
    offenders = sorted({r.auth for r in REGISTRY} - AUTH_VOCABULARY)
    assert not offenders, (
        f"routes declare auth value(s) no client implements: {offenders}. "
        f"Allowed: {sorted(AUTH_VOCABULARY)}."
    )


BROKEN_STATUSES = (500, 502)


def test_no_xfailed_route_expects_the_status_it_is_broken_with():
    """`expect` is what a FIXED route returns. Declaring 500 inverts the signal.

    An xfailed route whose `expect` is the status it returns while broken passes
    its own assertion today, so the tier reports XPASS while the defect is there
    and flips to xfailed the day somebody fixes it -- the opposite of what the
    spec asks for, and ten lines of permanent noise that train a reader to skip
    the XPASS block, which is exactly where a real fix announces itself.

    Server errors specifically, rather than any mismatch: what a working route
    returns is a judgement, but 500 is never it -- and neither is 502. This
    application returns a JSON 502 for its own upstream and data conditions, so
    502 is the status an xfailed entry is most likely to be tempted to declare,
    and check_gateway already lets that one through as "the application answering".
    """
    offenders = [
        (r.pattern, r.expect) for r in REGISTRY
        if r.xfail and set(BROKEN_STATUSES) & set(
            r.expect if isinstance(r.expect, tuple) else (r.expect,)
        )
    ]
    assert not offenders, (
        f"these xfailed routes expect one of {list(BROKEN_STATUSES)}, so they will "
        f"report XPASS while broken and xfailed once fixed: {offenders}"
    )


def test_every_placeholder_is_declared_and_every_declaration_is_used():
    """Task 6's fixture supplies exactly these names. An undeclared placeholder is
    a KeyError mid-sweep; an unused declaration is a fixture doing needless work."""
    used = set()
    for route in REGISTRY:
        used |= set(_PLACEHOLDER.findall(route.path or ""))
    undeclared = sorted(used - set(PLACEHOLDERS))
    unused = sorted(set(PLACEHOLDERS) - used)
    assert not undeclared, f"paths use placeholders PLACEHOLDERS does not define: {undeclared}"
    assert not unused, f"PLACEHOLDERS defines names no path uses: {unused}"


def test_the_discovery_fixture_resolves_exactly_the_declared_vocabulary():
    """The two halves of the placeholder contract, checked without a stack.

    ci/routes.py names the placeholders and the conftest `discovered` fixture
    resolves them. A name in one and not the other fails late and obscurely: an
    undeclared key is a KeyError in the middle of the sweep, and an unresolved one
    is a route that skips on every run in every environment and is never noticed.
    """
    assert DISCOVERED_KEYS == set(PLACEHOLDERS), (
        "the discovery fixture and PLACEHOLDERS have drifted apart.\n"
        f"  resolved but not declared: {sorted(DISCOVERED_KEYS - set(PLACEHOLDERS))}\n"
        f"  declared but not resolved: {sorted(set(PLACEHOLDERS) - DISCOVERED_KEYS)}"
    )


def test_every_placeholder_says_where_its_value_comes_from():
    for name, source in PLACEHOLDERS.items():
        assert source and source.strip(), f"{name} has no source description"


def test_an_enabled_route_is_requestable_and_an_excluded_one_is_not():
    for route in REGISTRY:
        if _enabled(route):
            assert route.path, f"{route.pattern} is enabled but has no path"
            assert route.methods, f"{route.pattern} is enabled but sends no method"
        else:
            assert route.profiles == frozenset(), (
                f"{route.pattern} is excluded but carries profiles {sorted(route.profiles)}"
            )
            assert route.exclude, f"{route.pattern} has no profiles and no exclude code"


def test_an_excluded_route_has_no_requestable_path():
    """Belt and braces on top of the client-side guard: a consumer that iterates
    the registry building URLs cannot even form one for an excluded route."""
    for route in REGISTRY:
        if route.exclude:
            assert route.path is None, (
                f"{route.pattern} is excluded but still carries path {route.path!r}"
            )


def test_the_registry_covers_the_measured_route_count():
    resolver_routes = [r for r in REGISTRY if r.resolver]
    non_resolver = [r for r in REGISTRY if not r.resolver]
    assert len(non_resolver) == NON_RESOLVER_COUNT, (
        f"{len(non_resolver)} resolver=False entries: {[r.pattern for r in non_resolver]}"
    )
    assert len(resolver_routes) == OWNED_ROUTE_COUNT, (
        f"the registry declares {len(resolver_routes)} resolver-owned routes, not "
        f"{OWNED_ROUTE_COUNT}. If the application really gained or lost a route, run "
        f"the completeness gate (ci/gate) -- it is the authority -- and update this "
        f"constant to what it reports."
    )


# The two paths conftest._probe_once requests, written out here rather than
# imported: the probe hard-codes them, and pinning a copy is what makes a change
# to either side show up as a red test instead of as a readiness gate that
# ProfileViolations on the box it was meant to certify.
READINESS_PROBE_PATHS = ("/login/", "/nextseek_api/people/current/")


def test_the_readiness_probe_paths_are_registered_for_every_profile():
    """The gate runs before any fixture and on every box, prod included.

    `startup rebuild` always passes --wait-ready, so these two requests are the
    first thing that happens after a deploy. If either route were ever narrowed to
    local and dev, the gate would refuse itself on the one box where a post-deploy
    check matters most -- and it would do it as a session error, not as a finding.
    """
    for path in READINESS_PROBE_PATHS:
        route = match("http://box" + path)
        assert route is not None, f"the readiness probe requests {path}, which is undeclared"
        missing = sorted(set(PROFILES) - set(route.profiles))
        assert not missing, (
            f"the readiness probe requests {path}, which is not enabled for {missing}"
        )


def _dummy_for(name: str) -> str:
    """A syntactically valid stand-in for one placeholder.

    Numeric for every id space -- they are all SEEK integer ids -- and a UID-shaped
    string for the one that is not. The VALUE is irrelevant to what these tests
    assert; only that it is the right shape to travel through the same pattern a
    real one would.
    """
    return "X.1" if name == "sample_uid" else "1"


def _fill(path: str) -> str:
    return _PLACEHOLDER.sub(lambda m: _dummy_for(m.group(1)), path)


def test_every_declared_path_resolves_back_to_its_own_entry():
    """Each entry's own path must match(), and must match THAT entry.

    Two things at once, and the second is the safety one. match() returns the most
    specific of the patterns that hit, so an entry whose path is swallowed by a
    broader sibling is an entry whose profiles, methods and exclusions are dead
    letters: the guard would consult the sibling's rules instead, silently. The
    first is a sanity check that the registry's own paths are requestable at all --
    a typo in a path is otherwise only found by a 404 mid-sweep.
    """
    wrong = []
    for route in REGISTRY:
        if not route.path:
            continue
        got = match("http://h" + _fill(route.path))
        if got is not route:
            wrong.append((route.pattern, route.path,
                          got.pattern if got is not None else "nothing"))
    assert not wrong, (
        "these entries do not resolve to themselves (pattern, path, what matched "
        f"instead): {wrong}"
    )


def test_no_enabled_route_shadows_an_excluded_one():
    """The same claim for the 26 entries that carry no path at all.

    An excluded entry is the whole of CI's defence for a route nobody may call: it
    has no path precisely so that no consumer can build a URL for it. But the guard
    still has to REFUSE the URL if one arrives by another door -- a redirect, a
    hand-written test, a link followed by the browser -- and it does that by
    match()ing it. If a broader enabled pattern won that match, the excluded entry
    would be unreachable and the URL would be permitted.

    The path is derived from the pattern with the gate's own suggest_path, which is
    a pure string helper: ci.gate.live_routes imports Django lazily, inside the two
    functions that need it, so importing it here needs no Django.
    """
    shadowed = []
    for route in REGISTRY:
        if not route.exclude:
            continue
        derived = _fill(suggest_path(route.pattern))
        got = match("http://h" + derived)
        if got is not route:
            shadowed.append((route.pattern, derived,
                             got.pattern if got is not None else "nothing"))
    assert not shadowed, (
        "these excluded routes are shadowed -- a request at their own URL resolves "
        f"to another entry, whose rules would be applied instead: {shadowed}"
    )


def test_t0_never_sweeps_a_write_auth_route_under_any_profile():
    """The sweep's central safety property, asserted of the function itself.

    `write` routes need the superuser account, and the whole suite is built on
    never holding it: T0's `clients` fixture has no fourth entry to call one with.
    That is a fact about a dict today and a comment tomorrow. This is the claim
    stated where it can fail: _callable_routes is pure, so it can be asked directly.
    """
    for profile in PROFILES:
        offenders = [r.pattern for r in _callable_routes(profile) if r.auth == "write"]
        assert not offenders, (
            f"_callable_routes({profile!r}) yields write-auth route(s): {offenders}"
        )


# --------------------------------------------------------------------------- #
# the browser-layer guard
# --------------------------------------------------------------------------- #

class _FakeRequest:
    def __init__(self, method: str, url: str) -> None:
        self.method = method
        self.url = url


class _FakeRoute:
    """Playwright's Route, reduced to the three things _guard_context touches."""

    def __init__(self, method: str, url: str) -> None:
        self.request = _FakeRequest(method, url)
        self.outcome: str | None = None

    def continue_(self) -> None:
        self.outcome = "continue"

    def abort(self) -> None:
        self.outcome = "abort"


class _FakeContext:
    """Playwright's BrowserContext, reduced to route() registration."""

    def __init__(self) -> None:
        self.handlers: list = []

    def route(self, pattern, handler) -> None:
        self.handlers.append((pattern, handler))


def _decide(profile: str, method: str, url: str) -> str:
    ctx = _FakeContext()
    _guard_context(ctx, profile)
    assert len(ctx.handlers) == 1, "expected exactly one handler to be installed"
    route = _FakeRoute(method, url)
    ctx.handlers[0][1](route)
    return route.outcome


def test_the_browser_guard_aborts_a_post_under_prod():
    assert _decide("prod", "POST", "http://box/nextseek_api/batch-upload/validate/") == "abort"


def test_the_browser_guard_lets_a_get_through_under_prod():
    assert _decide("prod", "GET", "http://box/seek/search/") == "continue"


def test_the_browser_guard_lets_the_login_post_through_under_prod():
    """The same carve-out ci/routes.py grants ^login, and for the same reason:
    authenticating is a precondition of reading anything."""
    assert _decide("prod", "POST", "http://box/login/") == "continue"


def test_the_browser_guard_installs_nothing_under_local():
    """Not a handler that says yes -- no handler at all. A page under local pulls
    fonts, bundles and XHR the registry does not describe, and intercepting every
    one of them to answer 'continue' would cost the flows real time for nothing."""
    ctx = _FakeContext()
    _guard_context(ctx, "local")
    assert ctx.handlers == []
