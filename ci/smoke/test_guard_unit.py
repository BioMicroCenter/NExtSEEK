"""The guard must refuse before sending. These tests make no network calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
import requests
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


def test_prod_permits_a_non_get_on_a_route_that_declares_the_carve_out(monkeypatch):
    """The one carve-out: a route may opt its non-GET methods into prod.

    Asserted by stubbing requests.Session.request, so the forwarded call is
    observed without any socket being opened.
    """
    monkeypatch.setattr(routes, "REGISTRY", [
        Route(pattern=r"^login", path="/login/", methods=("GET", "POST"),
              profiles="local,dev,prod", auth="anon", prod_allows_non_get=True),
    ])
    sent = []
    forwarded = object()

    def stub(self, method, url, *args, **kwargs):
        sent.append((method, url))
        return forwarded

    monkeypatch.setattr(requests.Session, "request", stub)

    s = GuardedSession(profile="prod")
    assert s.post("http://h/login/") is forwarded
    assert sent == [("POST", "http://h/login/")]


def test_prod_still_refuses_a_non_get_on_a_route_without_the_carve_out(monkeypatch):
    """The carve-out is per route, not a prod-wide relaxation."""
    def explode(self, method, url, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError(f"the guard forwarded {method} {url}")

    monkeypatch.setattr(requests.Session, "request", explode)

    s = GuardedSession(profile="prod")
    with pytest.raises(ProfileViolation, match="refused under the prod profile"):
        s.post("http://h/nextseek_api/sops/")
