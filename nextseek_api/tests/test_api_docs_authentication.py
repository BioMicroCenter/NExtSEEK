"""Issue #77 — the OpenAPI schema and its two doc UIs must require a login.

drf-spectacular's three serve-views assign
``permission_classes = spectacular_settings.SERVE_PERMISSIONS`` in their own class
bodies, and the package default is ``[AllowAny]``. Because the attribute is set
explicitly, the project-wide ``DEFAULT_PERMISSION_CLASSES = IsAuthenticated`` never
reaches them: registering the views plainly publishes the entire API surface — every
path, parameter, request body and model, ``admin/`` and ``evaluator/`` included — to
anyone who can reach the host.

The gate is deliberately ``IsAuthenticated`` and NOT an admin gate:

* ``IsAdminUser`` checks ``is_staff``, and ``dmac/views.py:80,97`` set ``is_staff = 1``
  on every SEEK user at login, so it is ``IsAuthenticated`` under a misleading name
  (see #74 / #75).
* ``IsSuperUser`` would over-gate. These routes are the working reference for
  legitimate token-holding API consumers; ``docs/endpoint-authorization-register.md``
  buckets all three as ``public-to-authenticated``.

``test_plain_user_*`` therefore uses an account that is neither staff nor superuser,
which pins BOTH halves of that ruling.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import get_resolver

DOC_ROUTES = (
    "/nextseek_api/schema/",
    "/nextseek_api/swagger/",
    "/nextseek_api/redoc/",
)

# A path that exists in the generated document. If an anonymous caller can read it,
# the disclosure is real and not merely a status code away.
DISCLOSED_MARKER = "/nextseek_api/evaluator/"


def _iter_routed_views(resolver=None, prefix=""):
    """Yield ``(path, callback)`` for every leaf route in the ROOT url conf."""
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        pattern = prefix + str(entry.pattern)
        if hasattr(entry, "url_patterns"):
            yield from _iter_routed_views(entry, pattern)
        else:
            yield pattern, entry.callback


def _effective_permission_classes(callback):
    """The permission classes a routed DRF view will actually instantiate.

    ``as_view(permission_classes=...)`` initkwargs override the class attribute, so
    reading ``callback.cls.permission_classes`` alone would miss a route-level gate.
    """
    initkwargs = getattr(callback, "initkwargs", None) or {}
    if "permission_classes" in initkwargs:
        return list(initkwargs["permission_classes"])
    view_cls = getattr(callback, "cls", None)
    return list(getattr(view_cls, "permission_classes", []) or [])


class TestApiDocsRequireAuthentication(TestCase):
    """#77: anonymous callers get nothing; any logged-in account gets everything."""

    def setUp(self):
        self.client = Client()
        self.plain_user = User.objects.create_user(
            username="issue77_plain",
            password="pw",
            is_staff=False,
            is_superuser=False,
        )

    def test_anonymous_is_refused_on_every_doc_route(self):
        for route in DOC_ROUTES:
            with self.subTest(route=route):
                resp = self.client.get(route)
                self.assertIn(
                    resp.status_code,
                    (401, 403),
                    f"{route} served an anonymous caller with {resp.status_code}",
                )

    def test_anonymous_schema_body_discloses_nothing(self):
        """Status codes can lie; assert the payload is gone too."""
        resp = self.client.get(DOC_ROUTES[0], HTTP_ACCEPT="application/json")
        body = resp.content.decode("utf-8", "replace")
        self.assertNotIn(
            DISCLOSED_MARKER,
            body,
            "anonymous /schema/ still returned the routed API surface",
        )

    def test_plain_user_can_read_every_doc_route(self):
        """Not an admin gate: is_staff=False, is_superuser=False must still work."""
        self.client.force_login(self.plain_user)
        for route in DOC_ROUTES:
            with self.subTest(route=route):
                resp = self.client.get(route)
                self.assertEqual(
                    resp.status_code,
                    200,
                    f"{route} refused an ordinary authenticated user",
                )

    def test_plain_user_still_gets_the_whole_document(self):
        """SERVE_PUBLIC stays on: the gate changes who, never what."""
        self.client.force_login(self.plain_user)
        resp = self.client.get(DOC_ROUTES[0], HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(DISCLOSED_MARKER, resp.content.decode("utf-8", "replace"))


class TestNoSpectacularRouteIsPublic(TestCase):
    """Structural guard: catches a fourth doc route added anywhere, later.

    The three routes in ``nextseek_api/urls.py`` are not the only place these views
    could be mounted — ``seek/urls.py`` carried a commented-out second copy until
    #77. This walks the whole ROOT url conf so uncommenting it, or adding a
    ``SpectacularJSONAPIView`` elsewhere, fails here instead of in production.
    """

    def test_no_drf_spectacular_view_is_served_with_allowany(self):
        from rest_framework.permissions import AllowAny

        found = []
        for path, callback in _iter_routed_views():
            view_cls = getattr(callback, "cls", None)
            module = getattr(view_cls, "__module__", "") or ""
            if not module.startswith("drf_spectacular"):
                continue
            perms = _effective_permission_classes(callback)
            found.append((path, view_cls.__name__, perms))
            self.assertNotIn(
                AllowAny,
                perms,
                f"/{path} serves {view_cls.__name__} to unauthenticated callers",
            )
            self.assertTrue(
                perms,
                f"/{path} serves {view_cls.__name__} with an empty permission list",
            )

        self.assertEqual(
            len(found),
            3,
            f"expected exactly the 3 documentation routes, found {found!r}",
        )
