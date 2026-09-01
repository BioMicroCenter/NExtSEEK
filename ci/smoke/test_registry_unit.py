"""Unit tests for the route registry. No network, no stack, no Django."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from ci.routes import Route, match, EXCLUDE_CODES, _check_unique_patterns


def test_profiles_string_is_normalised_to_a_frozenset():
    r = Route(pattern=r"^x/$", path="/x/", methods=("GET",), profiles="local,dev")
    assert r.profiles == frozenset({"local", "dev"})


def test_substring_cannot_masquerade_as_a_profile():
    """The bug this normalisation exists to prevent: 'od' in 'local,dev,prod' is True."""
    r = Route(pattern=r"^x/$", path="/x/", methods=("GET",), profiles="local,dev,prod")
    assert "od" not in r.profiles
    assert "prod" in r.profiles


def test_unknown_profile_is_refused():
    with pytest.raises(ValueError, match="unknown profile"):
        Route(pattern=r"^x/$", path="/x/", methods=("GET",), profiles="local,staging")


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


def test_methods_are_normalised_to_upper_case():
    r = Route(pattern=r"^x/$", path="/x/", methods=("get", " post "), profiles="dev")
    assert r.methods == ("GET", "POST")


def test_a_malformed_pattern_is_refused_at_construction():
    """A cached matcher would otherwise raise mid-sweep, inside an unrelated caller."""
    with pytest.raises(ValueError, match="not a usable regex"):
        Route(pattern=r"^x/(unclosed/$", path="/x/", methods=("GET",), profiles="dev")


# --------------------------------------------------------------------------- #
# resolver / prod_allows_non_get
# --------------------------------------------------------------------------- #

def test_resolver_defaults_to_true_and_can_be_turned_off():
    declared = Route(pattern=r"^x/$", path="/x/", methods=("GET",), profiles="dev")
    assert declared.resolver is True
    unrouted = Route(pattern=r"^static/css/site.css$", path="/static/css/site.css",
                     methods=("GET",), profiles="dev", resolver=False)
    assert unrouted.resolver is False


def test_prod_allows_non_get_is_accepted_on_a_prod_non_get_route():
    r = Route(pattern=r"^login$", path="/login/", methods=("GET", "POST"),
              profiles="local,dev,prod", prod_allows_non_get=True)
    assert r.prod_allows_non_get is True


def test_prod_allows_non_get_requires_the_prod_profile():
    with pytest.raises(ValueError, match="prod_allows_non_get"):
        Route(pattern=r"^login$", path="/login/", methods=("GET", "POST"),
              profiles="local,dev", prod_allows_non_get=True)


def test_prod_allows_non_get_requires_a_non_get_method():
    with pytest.raises(ValueError, match="prod_allows_non_get"):
        Route(pattern=r"^login$", path="/login/", methods=("GET",),
              profiles="local,dev,prod", prod_allows_non_get=True)


def test_a_lower_case_get_cannot_pass_as_a_non_get_method():
    """methods is normalised first, so ("get",) is still GET-only."""
    with pytest.raises(ValueError, match="prod_allows_non_get"):
        Route(pattern=r"^login$", path="/login/", methods=("get",),
              profiles="local,dev,prod", prod_allows_non_get=True)


# --------------------------------------------------------------------------- #
# the matcher
# --------------------------------------------------------------------------- #

def test_matcher_strips_nested_anchors_from_a_concatenated_pattern():
    """Django patterns concatenate as '^seek/^sample/...$'. The matcher must cope."""
    r = Route(pattern=r"^seek/^sample/id=(?P<id>\d+)/$",
              path="/seek/sample/id={sample_id}/", methods=("GET",), profiles="dev")
    assert r.matches("/seek/sample/id=334598/")
    assert not r.matches("/seek/sample/id=abc/")


def test_matcher_keeps_the_caret_that_negates_a_character_class():
    """Every DRF detail route spells its pk '[^/.]+'; stripping that caret inverts it."""
    r = Route(pattern=r"^nextseek_api/^^sops/(?P<pk>[^/.]+)/$",
              path="/nextseek_api/sops/{pk}/", methods=("GET",), profiles="dev")
    assert r.matches("/nextseek_api/sops/12/")
    assert not r.matches("/nextseek_api/sops/1/2/")


def test_matcher_keeps_a_character_class_that_admits_dots():
    r = Route(pattern=r"^nextseek_api/^^assays/(?P<uid>[^/]+)/$",
              path="/nextseek_api/assays/{uid}/", methods=("GET",), profiles="dev")
    assert r.matches("/nextseek_api/assays/A.B-1/")


def test_matcher_keeps_an_escaped_dollar_as_a_literal():
    r = Route(pattern=r"^seek/price\$/$", path="/seek/price$/",
              methods=("GET",), profiles="dev")
    assert r.matches("/seek/price$/")
    assert not r.matches("/seek/price/")


def test_a_pattern_without_a_terminal_anchor_is_a_prefix_match():
    """Django's re_path('^login') resolves '/login/'. Prefix semantics are load-bearing."""
    r = Route(pattern=r"^login", path="/login/", methods=("GET",), profiles="dev")
    assert r.matches("/login/")
    assert r.matches("/login")


def test_a_nested_pattern_without_a_terminal_anchor_is_also_a_prefix_match():
    r = Route(pattern=r"^seek/^samples/query/", path="/seek/samples/query/",
              methods=("GET",), profiles="dev")
    assert r.matches("/seek/samples/query/")
    assert r.matches("/seek/samples/query/x")


def test_a_pattern_with_a_terminal_anchor_matches_the_whole_path():
    r = Route(pattern=r"^seek/^help/$", path="/seek/help/", methods=("GET",),
              profiles="dev")
    assert r.matches("/seek/help/")
    assert not r.matches("/seek/help/x")


def test_a_terminal_anchor_pins_the_trailing_slash_too():
    r = Route(pattern=r"^logout$", path="/logout", methods=("GET",), profiles="dev")
    assert r.matches("/logout")
    assert not r.matches("/logout/")


# --------------------------------------------------------------------------- #
# match()
# --------------------------------------------------------------------------- #

def test_match_finds_the_route_for_a_full_url(monkeypatch):
    sops = Route(pattern=r"^nextseek_api/^^sops/$", path="/nextseek_api/sops/",
                 methods=("GET",), profiles="dev")
    monkeypatch.setattr("ci.routes.REGISTRY", [sops])
    assert match("http://127.0.0.1:8000/nextseek_api/sops/") is sops
    assert match("http://127.0.0.1:8000/nextseek_api/nope/") is None


def test_match_returns_the_route_whose_pattern_covers_the_path(monkeypatch):
    """Two non-overlapping exact routes: selection is by pattern, not by position."""
    root = Route(pattern=r"^nextseek_api/^$", path="/nextseek_api/",
                 methods=("GET",), profiles="dev")
    sops = Route(pattern=r"^nextseek_api/^^sops/$", path="/nextseek_api/sops/",
                 methods=("GET",), profiles="dev")
    monkeypatch.setattr("ci.routes.REGISTRY", [root, sops])
    assert match("/nextseek_api/") is root
    assert match("/nextseek_api/sops/") is sops


@pytest.mark.parametrize("reverse", [False, True], ids=["declared-first", "declared-last"])
def test_match_prefers_a_literal_action_over_the_detail_route_that_swallows_it(
    monkeypatch, reverse
):
    """Django resolves '/attributes/search/' to the action, not the pk detail route.

    Both patterns are exact and both match, and the detail route is the LONGER string,
    so ranking on raw length would return the wrong one. Ranking on literal characters,
    with the regex group discounted, reproduces Django's answer in either order.
    """
    action = Route(pattern=r"^nextseek_api/^^attributes/search/$",
                   path="/nextseek_api/attributes/search/",
                   methods=("GET",), profiles="dev")
    detail = Route(pattern=r"^nextseek_api/^^attributes/(?P<pk>[^/.]+)/$",
                   path="/nextseek_api/attributes/{pk}/",
                   methods=("GET",), profiles="dev")
    assert len(detail.pattern) > len(action.pattern)
    registry = [detail, action] if reverse else [action, detail]
    monkeypatch.setattr("ci.routes.REGISTRY", registry)
    assert match("/nextseek_api/attributes/search/") is action
    assert match("/nextseek_api/attributes/17/") is detail


def test_match_prefers_an_exact_route_over_an_overlapping_prefix(monkeypatch):
    """'^login' matches '/login/special/' too, so list order must not decide."""
    prefix = Route(pattern=r"^login", path="/login/", methods=("GET",), profiles="dev")
    exact = Route(pattern=r"^login/special/$", path="/login/special/",
                  methods=("GET",), profiles="dev")
    monkeypatch.setattr("ci.routes.REGISTRY", [prefix, exact])
    assert match("/login/special/") is exact
    assert match("/login/") is prefix


# --------------------------------------------------------------------------- #
# _check_unique_patterns
# --------------------------------------------------------------------------- #

def test_unique_patterns_accepts_distinct_declarations():
    routes = [
        Route(pattern=r"^a/$", path="/a/", methods=("GET",), profiles="dev"),
        Route(pattern=r"^b/$", path="/b/", methods=("GET",), profiles="dev"),
    ]
    assert _check_unique_patterns(routes) is None


def test_unique_patterns_refuses_a_repeated_pattern():
    """The second entry would be unreachable through match(); that is a declaration bug."""
    routes = [
        Route(pattern=r"^a/$", path="/a/", methods=("GET",), profiles="dev"),
        Route(pattern=r"^a/$", path="/a/", methods=("GET",), profiles="prod"),
    ]
    with pytest.raises(ValueError, match="duplicate pattern"):
        _check_unique_patterns(routes)
