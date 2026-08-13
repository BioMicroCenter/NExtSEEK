"""Self-ingest of our OWN OpenAPI schema route (#94).

`#77` put `IsAuthenticated` on `/nextseek_api/schema/`, which broke every
in-tree consumer that fetched that URL anonymously: `fetch_schema` died with
`SCHEMA_FETCH_FAILED` / "401 Client Error: Unauthorized".

The fix serves our own schema document in-process (drf-spectacular's
`SchemaGenerator`, exactly what `SpectacularAPIView` serves) instead of going
back out over HTTP to a route we ourselves gate.

Settings hygiene: this lane copies a gitignored `dmac/local_settings.py` into
the worktree, so ambient `ALLOWED_HOSTS` / `NEXTSEEK_*` values could make these
tests pass for the wrong reason. Every test therefore pins `ALLOWED_HOSTS` with
`override_settings` and pins the env with the `_env` context manager below.
"""

import ast
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

INTERNAL = "http://127.0.0.1:8000"
INTERNAL_HOST = "127.0.0.1"
PUBLIC_HOST = "nextseek-dev.example.mit.edu"
FOREIGN_HOST = "petstore3.swagger.io"

MINIMAL_DOC = '{"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "paths": {}}'


def _env(**overrides):
    """Env with all base-URL keys cleared, then the given overrides applied.

    Same shape as `test_schema_processor._env`; duplicated rather than imported
    so this module does not depend on another test module's internals.
    """
    base = {
        "NEXTSEEK_INTERNAL_BASE_URL": "",
        "NEXTSEEK_BASE_URL": "",
        "NEXTSEEK_HOSTNAME": "",
        "NEXTSEEK_PROD_URL": "",
    }
    base.update(overrides)
    return patch.dict(os.environ, base, clear=False)


def _sp():
    """Import the module under test lazily.

    Keeps this file collectable against the pre-fix tree, so the behavioural
    tests below report the real failure instead of a collection error.
    """
    from nextseek_api.schema_rag import schema_processor

    return schema_processor


def _http_response(text=MINIMAL_DOC):
    response = Mock()
    response.status_code = 200
    response.text = text
    response.headers = {"Content-Type": "application/json"}
    response.raise_for_status = Mock()
    return response


# ---------------------------------------------------------------------------
# Task 1 — self-schema detection
# ---------------------------------------------------------------------------


@override_settings(ALLOWED_HOSTS=[PUBLIC_HOST])
class TestIsSelfSchemaUrl(TestCase):
    """`is_self_schema_url` must require BOTH host and path to be ours."""

    def test_own_public_host_on_the_schema_route_is_self(self):
        with _env():
            self.assertTrue(
                _sp().is_self_schema_url(f"https://{PUBLIC_HOST}/nextseek_api/schema/")
            )

    def test_own_host_on_a_different_path_is_not_self(self):
        """Host-only matching would be a security bug: an arbitrary path on our
        own host is still an arbitrary fetch."""
        with _env():
            self.assertFalse(
                _sp().is_self_schema_url(f"https://{PUBLIC_HOST}/nextseek_api/samples/")
            )
            self.assertFalse(
                _sp().is_self_schema_url(f"https://{PUBLIC_HOST}/")
            )
            # A path that merely *contains* the route must not match either.
            self.assertFalse(
                _sp().is_self_schema_url(
                    f"https://{PUBLIC_HOST}/nextseek_api/schema/../evil"
                )
            )

    def test_foreign_host_on_the_schema_route_is_not_self(self):
        with _env():
            self.assertFalse(
                _sp().is_self_schema_url(f"https://{FOREIGN_HOST}/nextseek_api/schema/")
            )

    def test_internal_base_url_host_is_ours(self):
        """The internal base host is NOT in ALLOWED_HOSTS, but self-ingest via
        the rewritten internal URL still has to be recognised."""
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            self.assertTrue(
                _sp().is_self_schema_url(f"{INTERNAL}/nextseek_api/schema/")
            )

    def test_own_hosts_unions_public_and_internal(self):
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            hosts = _sp()._own_hosts()
        self.assertIn(PUBLIC_HOST, hosts)
        self.assertIn(INTERNAL_HOST, hosts)

    def test_own_public_hosts_is_left_alone(self):
        """`resolve_transport_url` keys off `_own_public_hosts`; adding the
        internal host there would change its behaviour, so it must not."""
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            public = _sp()._own_public_hosts()
        self.assertIn(PUBLIC_HOST, public)
        self.assertNotIn(INTERNAL_HOST, public)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_wildcard_allowed_hosts_does_not_make_the_world_ours(self):
        with _env():
            self.assertFalse(
                _sp().is_self_schema_url(f"https://{FOREIGN_HOST}/nextseek_api/schema/")
            )

    def test_non_url_strings_are_not_self(self):
        with _env():
            for value in ("/nextseek_api/schema/", "not a url", "", None):
                self.assertFalse(_sp().is_self_schema_url(value), value)

    def test_query_string_and_trailing_slash_variants_still_match(self):
        with _env():
            for url in (
                f"https://{PUBLIC_HOST}/nextseek_api/schema/?format=yaml",
                f"https://{PUBLIC_HOST}/nextseek_api/schema?format=json#top",
                f"https://{PUBLIC_HOST}/nextseek_api/schema",
                f"http://{PUBLIC_HOST}:9001/nextseek_api/schema/",
            ):
                self.assertTrue(_sp().is_self_schema_url(url), url)

    def test_route_path_matches_the_reversed_url(self):
        from django.urls import reverse

        self.assertEqual(_sp().self_schema_route_path(), reverse("nextseek_api:schema"))


# ---------------------------------------------------------------------------
# Task 2 — in-process generation in fetch_schema
# ---------------------------------------------------------------------------


@override_settings(ALLOWED_HOSTS=[PUBLIC_HOST])
class TestFetchSchemaServesSelfInProcess(TestCase):

    def test_self_url_is_served_without_any_http_call(self):
        """The #94 defect itself: this GET used to 401."""
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            with patch(
                "nextseek_api.schema_rag.schema_processor.requests.get",
                side_effect=AssertionError("fetch_schema must NOT go over HTTP for our own schema route"),
            ):
                doc = _sp().OpenAPISchemaProcessor().fetch_schema(
                    f"https://{PUBLIC_HOST}/nextseek_api/schema/"
                )

        self.assertIsInstance(doc, dict)
        self.assertIn("paths", doc)
        self.assertGreater(len(doc["paths"]), 0)
        self.assertIn("openapi", doc)

    def test_generator_raising_falls_back_to_the_internal_http_url(self):
        """Strictly additive: any generation failure must fall through to the
        pre-#94 HTTP path, and must never propagate out of fetch_schema."""
        boom = Mock(side_effect=RuntimeError("generator exploded"))
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            with patch("drf_spectacular.generators.SchemaGenerator", boom):
                with patch(
                    "nextseek_api.schema_rag.schema_processor.requests.get",
                    return_value=_http_response(),
                ) as mock_get:
                    doc = _sp().OpenAPISchemaProcessor().fetch_schema(
                        f"https://{PUBLIC_HOST}/nextseek_api/schema/"
                    )

        self.assertEqual(mock_get.call_args[0][0], f"{INTERNAL}/nextseek_api/schema/")
        self.assertEqual(doc["openapi"], "3.0.0")

    def test_generator_returning_a_non_dict_falls_back_to_http(self):
        gen = Mock()
        gen.return_value.get_schema.return_value = "not a dict"
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            with patch("drf_spectacular.generators.SchemaGenerator", gen):
                with patch(
                    "nextseek_api.schema_rag.schema_processor.requests.get",
                    return_value=_http_response(),
                ) as mock_get:
                    doc = _sp().OpenAPISchemaProcessor().fetch_schema(
                        f"https://{PUBLIC_HOST}/nextseek_api/schema/"
                    )

        self.assertEqual(mock_get.call_args[0][0], f"{INTERNAL}/nextseek_api/schema/")
        self.assertEqual(doc["openapi"], "3.0.0")

    def test_external_url_never_consults_the_generator(self):
        gen = Mock()
        url = f"https://{FOREIGN_HOST}/api/v3/openapi.json"
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            with patch("drf_spectacular.generators.SchemaGenerator", gen):
                with patch(
                    "nextseek_api.schema_rag.schema_processor.requests.get",
                    return_value=_http_response(),
                ) as mock_get:
                    _sp().OpenAPISchemaProcessor().fetch_schema(url)

        gen.assert_not_called()
        self.assertEqual(mock_get.call_args[0][0], url)

    def test_own_host_but_not_the_schema_route_still_goes_over_http(self):
        gen = Mock()
        url = f"https://{PUBLIC_HOST}/nextseek_api/something_else.json"
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            with patch("drf_spectacular.generators.SchemaGenerator", gen):
                with patch(
                    "nextseek_api.schema_rag.schema_processor.requests.get",
                    return_value=_http_response(),
                ) as mock_get:
                    _sp().OpenAPISchemaProcessor().fetch_schema(url)

        gen.assert_not_called()
        self.assertEqual(
            mock_get.call_args[0][0],
            f"{INTERNAL}/nextseek_api/something_else.json",
        )


@override_settings(ALLOWED_HOSTS=[PUBLIC_HOST])
class TestIngestSchemaSelfIngest(TestCase):
    """The issue's own confirm-fixed recipe, driven through the service layer
    (`nextseek_api/schema_rag/service.py`, which is out of this fixer's file
    set and is NOT edited)."""

    def test_ingesting_our_own_schema_route_succeeds(self):
        from nextseek_api.models import IngestRequest
        from nextseek_api.schema_rag.service import ingest_schema

        with tempfile.TemporaryDirectory() as duckdb_dir:
            with override_settings(SCHEMA_RAG_DUCKDB_DIR=duckdb_dir):
                with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
                    with patch(
                        "nextseek_api.schema_rag.schema_processor.requests.get",
                        side_effect=AssertionError("self-ingest must not go over HTTP"),
                    ):
                        result = ingest_schema(
                            IngestRequest(
                                schema_url=f"https://{PUBLIC_HOST}/nextseek_api/schema/"
                            )
                        )

        self.assertTrue(result.success, getattr(result.error, "details", result.error))
        self.assertGreater(result.response.num_endpoints, 0)


# ---------------------------------------------------------------------------
# Task 4 — pin the documented example
# ---------------------------------------------------------------------------


class TestDocumentedSelfIngestExample(TestCase):
    """`services/schema_rag.py` advertises a self-ingest `schema_url` in its
    published `OpenApiExample`s. That URL is the thing #94 says is broken, so
    pin it to the real schema route: an edit that moves it off the self-ingest
    path silently un-documents the fixed behaviour.
    """

    def _documented_schema_urls(self):
        source = Path(
            __file__
        ).resolve().parent.parent.joinpath("services", "schema_rag.py").read_text()
        tree = ast.parse(source)
        urls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "schema_url"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    urls.append(value.value)
        return urls

    def test_the_example_pointing_at_a_nextseek_host_uses_the_schema_route(self):
        from urllib.parse import urlsplit

        from django.urls import reverse

        route = reverse("nextseek_api:schema").rstrip("/")
        urls = self._documented_schema_urls()
        self.assertTrue(urls, "no schema_url examples found in services/schema_rag.py")

        self_urls = [u for u in urls if "nextseek" in (urlsplit(u).hostname or "")]
        self.assertTrue(
            self_urls,
            "services/schema_rag.py no longer documents a self-ingest schema_url; "
            "#94's own confirm-fixed recipe has been removed from the API docs",
        )
        for url in self_urls:
            self.assertEqual(urlsplit(url).path.rstrip("/"), route, url)

    def test_the_documented_example_is_recognised_as_a_self_schema_url(self):
        from urllib.parse import urlsplit

        urls = [
            u
            for u in self._documented_schema_urls()
            if "nextseek" in (urlsplit(u).hostname or "")
        ]
        self.assertTrue(urls)
        for url in urls:
            host = urlsplit(url).hostname
            with override_settings(ALLOWED_HOSTS=[host]):
                with _env():
                    self.assertTrue(_sp().is_self_schema_url(url), url)
