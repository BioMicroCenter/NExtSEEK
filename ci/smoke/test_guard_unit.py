"""The guard must refuse before sending. These tests make no network calls."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
import requests
from ci import routes
from ci.routes import Route
from ci.smoke.client import GuardedSession, ProfileViolation

# Captured before any fixture patches it, so the redirect test can put the real
# dispatch machinery back while the autouse stubs keep every other test off the
# network.
_REAL_SESSION_REQUEST = requests.Session.request


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

    # No test in this file may open a socket. Both layers a request could leave
    # through are stubbed, so a regression in the guard fails on an assertion
    # here rather than on a DNS lookup. Tests that need one of them re-patch the
    # same attribute, and monkeypatch lets the later setattr win.
    def no_network(self, *args, **kwargs):
        raise AssertionError(
            "a request escaped the guard; no test in this file may reach the network"
        )

    monkeypatch.setattr(requests.Session, "request", no_network)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", no_network)


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
    with pytest.raises(ProfileViolation, match="not enabled"):
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


def test_prod_still_refuses_a_non_get_on_a_route_without_the_carve_out():
    """The carve-out is per route, not a prod-wide relaxation."""
    s = GuardedSession(profile="prod")
    with pytest.raises(ProfileViolation, match="refused under the prod profile"):
        s.post("http://h/nextseek_api/sops/")


def test_send_is_guarded_as_well_as_request():
    """A caller holding a PreparedRequest bypasses request() entirely."""
    s = GuardedSession(profile="dev")
    prepared = requests.Request("GET", "http://h/nextseek_api/not_declared/").prepare()
    with pytest.raises(ProfileViolation, match="unregistered"):
        s.send(prepared)


def test_a_redirect_hop_is_checked_too(monkeypatch):
    """requests follows a 3xx by re-entering send(), never request()."""
    monkeypatch.setattr(requests.Session, "request", _REAL_SESSION_REQUEST)

    def redirect_to_an_excluded_route(self, request, **kwargs):
        r = requests.Response()
        r.status_code = 302
        r.headers["Location"] = "http://h/seek/admin/clades/syncSampleTypes/"
        r.url = request.url
        r.request = request
        r.raw = io.BytesIO(b"")
        r._content = b""
        return r

    monkeypatch.setattr(
        requests.adapters.HTTPAdapter, "send", redirect_to_an_excluded_route
    )

    s = GuardedSession(profile="local")
    with pytest.raises(ProfileViolation, match="not enabled"):
        s.get("http://h/nextseek_api/sops/", allow_redirects=True)


def test_a_bound_session_refuses_another_host():
    s = GuardedSession(profile="local", base_url="http://h")
    with pytest.raises(ProfileViolation, match="off-instance"):
        s.get("http://other/nextseek_api/sops/")


def test_a_bound_session_still_forwards_a_url_on_its_own_host(monkeypatch):
    sent = []
    forwarded = object()

    def stub(self, method, url, *args, **kwargs):
        sent.append((method, url))
        return forwarded

    monkeypatch.setattr(requests.Session, "request", stub)

    s = GuardedSession(profile="local", base_url="http://h")
    assert s.get("http://h/nextseek_api/sops/") is forwarded
    assert sent == [("GET", "http://h/nextseek_api/sops/")]


def test_a_constructor_kwarg_cannot_replace_the_guard():
    with pytest.raises(TypeError, match="request"):
        GuardedSession(profile="local", request=lambda *a, **k: None)
