"""Safety rules asserted against the REAL registry. No network, no stack, no Django.

test_registry_unit.py proves the Route dataclass enforces its own invariants on
a synthetic entry. This module proves the invariants hold of every entry that is
actually declared, so a future edit that breaks one is a red test rather than a
silent widening of what CI is allowed to do to production.

Each assertion below corresponds to a rule stated in
docs/superpowers/specs/2026-09-01-nextseek-ci-comprehensive-coverage-design.md.
They are here so that relaxing a rule takes a deliberate deletion.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.routes import PLACEHOLDERS, REGISTRY, _check_unique_patterns
from ci.smoke.conftest import DISCOVERED_KEYS

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
