"""Driven from the URL conf: a pass is refused on every route and method outside the allow table.

A new endpoint is refused until someone lists it in nextseek_api/assistant/turn_pass_allow.py. Three views never
read the pass at all (CCAssistantViewSet, PeopleProxyViewSet, GraphSearchViewSet), so to them a pass is no
credential.
"""
import itertools

import pytest
from django.urls import NoReverseMatch, URLResolver, get_resolver, reverse
from django.urls.resolvers import ResolverMatch
from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.test import APIClient, APIRequestFactory

from nextseek_api.assistant import turn_pass_allow
from nextseek_api.assistant.turn_pass_auth import PassNotAllowed, TurnPassAuthentication
from nextseek_api.tests.turn_pass_support import make_turn, pass_header

pytestmark = pytest.mark.django_db

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
ALLOWED = {(row.route_name, method) for row in turn_pass_allow.ALLOW_TABLE for method in row.methods}
# Values tried for each named group when a concrete path is needed (digits satisfy the hex UUID patterns too).
_CANDIDATES = ("1", "0b5c6d9e-1f2a-4b3c-8d4e-5f6a7b8c9d0e", "abc", "json")


def _patterns():
    """(namespaces, URLPattern) for every pattern in the URL conf, however deeply included."""
    found = []

    def walk(patterns, namespaces):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, namespaces + ([pattern.namespace] if pattern.namespace else []))
            else:
                found.append((namespaces, pattern))

    walk(get_resolver().url_patterns, [])
    return found


def _auth_classes(callback):
    cls = getattr(callback, "cls", None)
    if cls is None:
        return None  # not a DRF view: DRF authentication never runs there
    initkwargs = getattr(callback, "initkwargs", None) or {}
    return list(initkwargs.get("authentication_classes") or cls.authentication_classes)


def _concrete_path(name, pattern):
    groups = list(pattern.pattern.regex.groupindex)
    # DRF negotiates a format suffix before it authenticates, and an unknown format is a 404: give the `format`
    # group a renderer format the views have.
    choices = [("json",) if group == "format" else _CANDIDATES for group in groups]
    for values in itertools.product(*choices):
        try:
            return reverse(name, kwargs=dict(zip(groups, values)))
        except NoReverseMatch:
            continue
    return None


def _view_methods(callback):
    actions = getattr(callback, "actions", None)
    if actions:
        return sorted(method.upper() for method in actions if method not in ("head", "options"))
    return [method.upper() for method in ("get", "post", "put", "patch", "delete") if hasattr(callback.cls, method)]


def test_every_allow_row_names_a_route_whose_view_reads_the_pass():
    reading = {}
    for namespaces, pattern in _patterns():
        classes = _auth_classes(pattern.callback)
        if pattern.name and classes is not None:
            reading.setdefault(":".join(namespaces + [pattern.name]), TurnPassAuthentication in classes)
    for row in turn_pass_allow.ALLOW_TABLE:
        assert row.route_name in reading, f"{row.route_name} names no route in the URL conf"
        assert reading[row.route_name], f"{row.route_name}'s view does not read the pass"


def test_authenticate_refuses_every_route_and_method_outside_the_table():
    turn, raw = make_turn()
    factory = APIRequestFactory()
    names, checked = set(), 0
    for namespaces, pattern in _patterns():
        match = ResolverMatch(pattern.callback, (), {}, url_name=pattern.name, namespaces=namespaces)
        names.add(match.view_name)
        for method in METHODS:
            if (match.view_name, method) in ALLOWED:
                continue
            django_request = factory.generic(method, "/walk/", **pass_header(raw))
            django_request.resolver_match = match
            with pytest.raises(PassNotAllowed):
                TurnPassAuthentication().authenticate(Request(django_request))
            checked += 1
    assert {"nextseek_api:assistant-api-write", "nextseek_api:cc-assistant-cc-query-async",
            "nextseek_api:assistant-query"} <= names
    assert checked > 500, f"the walk covered only {checked} route/method pairs"


def test_every_method_the_table_does_not_list_is_refused_over_http():
    """Every view that reads the pass, every method it serves: refused in authenticate, before the view runs, so
    nothing here reaches SEEK, a model or the database. The body is not parsed for a refused route."""
    turn, raw = make_turn()
    client = APIClient()
    unreversed, sent = [], 0
    for namespaces, pattern in _patterns():
        classes = _auth_classes(pattern.callback)
        if not pattern.name or not classes or TurnPassAuthentication not in classes:
            continue
        name = ":".join(namespaces + [pattern.name])
        path = _concrete_path(name, pattern)
        if path is None:
            unreversed.append(name)
            continue
        for method in _view_methods(pattern.callback):
            if (name, method) in ALLOWED:
                continue
            resp = client.generic(method, path, **pass_header(raw))
            assert resp.status_code == 403, f"{method} {path} ({name}) -> {resp.status_code}"
            # Some views render errors with their own renderer (YAML for the schema view): match the bytes.
            # The swagger UI page renders every error as bare HTML ("403 Forbidden"): the status is the proof there.
            if name not in ("nextseek_api:swagger-ui", "nextseek_api:redoc"):
                assert b"PASS_NOT_ALLOWED" in resp.content, f"{method} {path} ({name})"
            sent += 1
    assert unreversed == [], f"add a candidate value so these routes get a path: {unreversed}"
    assert sent > 100, f"only {sent} requests were sent"


def test_only_the_default_list_and_the_two_named_viewsets_read_the_pass():
    from nextseek_api.batch_upload.views import BatchUploadViewSet
    from nextseek_api.services.assistant import AssistantViewSet
    from nextseek_api.services.cc_assistant import CCAssistantViewSet
    from nextseek_api.services.graph_search import GraphSearchViewSet
    from nextseek_api.services.people import PeopleProxyViewSet

    # A list set in this repository's own code. Third-party views (drf-spectacular's schema views) copy the default
    # list into their class body; they read the pass because the default list does, and the walk above refuses it.
    repo_packages = {"nextseek_api", "seek", "dmac", "api_app"}
    explicit_readers = set()
    for _namespaces, pattern in _patterns():
        cls = getattr(pattern.callback, "cls", None)
        if cls is None:
            continue
        # An @api_view function view gets a generated WrappedAPIView (named for its module) whose class body copies
        # the default list: that is the default, not an explicit list.
        own_list = any("authentication_classes" in klass.__dict__
                       and list(klass.__dict__["authentication_classes"]) != list(api_settings.DEFAULT_AUTHENTICATION_CLASSES)
                       for klass in cls.__mro__ if klass.__module__.split(".")[0] in repo_packages)
        if own_list and TurnPassAuthentication in _auth_classes(pattern.callback):
            explicit_readers.add(cls)
    assert explicit_readers == {AssistantViewSet, BatchUploadViewSet}
    assert api_settings.DEFAULT_AUTHENTICATION_CLASSES[0] is TurnPassAuthentication
    assert AssistantViewSet.authentication_classes[0] is TurnPassAuthentication
    assert BatchUploadViewSet.authentication_classes[0] is TurnPassAuthentication
    for cls in (CCAssistantViewSet, GraphSearchViewSet, PeopleProxyViewSet):
        assert TurnPassAuthentication not in cls.authentication_classes
