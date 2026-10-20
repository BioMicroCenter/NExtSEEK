"""Who may make schema_rag fetch a schema, and what a fetched schema may point at (#64).

* A ``$ref`` is resolved over http and https only. Any other scheme is refused before
  anything is opened.
* Ingesting a schema from a URL is superuser only: the ingest route, and the retrieve
  route when its ``schema_url`` would be ingested. Every account may still retrieve by
  ``session_id``, and may name this instance's own schema, which is built in process.
"""
import json
import os
import tempfile
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from NessieAI.schema_rag import schema_processor
from NessieAI.schema_rag.errors import SchemaFetchError
from NessieAI.schema_rag.schema_processor import OpenAPISchemaProcessor

INGEST = "/nextseek_api/schema_rag/ingest/"
RETRIEVE = "/nextseek_api/schema_rag/retrieve/"
FOREIGN = "http://schemas.example.org/openapi.json"


def _doc_with_tags_ref(ref):
    return {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {"/x": {"get": {
            "operationId": "x",
            "tags": {"$ref": ref},
            "responses": {"200": {"description": "ok"}},
        }}},
    }


class RefSchemes(TestCase):
    """The $ref loader opens http and https only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.local = os.path.join(self.tmp, "local.json")
        with open(self.local, "w") as fh:
            json.dump({"value": "local-file-content"}, fh)

    def _resolve(self, doc):
        return OpenAPISchemaProcessor()._resolve_refs(doc, "http://schemas.example.org/s.json")

    def test_a_file_ref_is_refused_and_never_opened(self):
        with patch("urllib.request.urlopen") as opened:
            with pytest.raises(SchemaFetchError) as raised:
                self._resolve(_doc_with_tags_ref("file://" + self.local))
        opened.assert_not_called()
        assert "local-file-content" not in str(raised.value)

    def test_other_non_http_schemes_are_refused(self):
        for ref in ("ftp://files.example.org/x.json", "data:application/json,{}", "relative.json"):
            with pytest.raises(SchemaFetchError):
                self._resolve(_doc_with_tags_ref(ref))

    def test_an_http_ref_still_resolves(self):
        class _Resp:
            def raise_for_status(self):
                pass

            def json(self, **kwargs):
                return ["Samples"]

        with patch("requests.get", return_value=_Resp()) as fetched:
            out = self._resolve(_doc_with_tags_ref("https://schemas.example.org/tags.json"))
        fetched.assert_called_once_with("https://schemas.example.org/tags.json")
        assert out["paths"]["/x"]["get"]["tags"] == ["Samples"]

    def test_an_internal_ref_still_resolves(self):
        doc = _doc_with_tags_ref("#/components/tags")
        doc["components"] = {"tags": ["Samples"]}
        assert self._resolve(doc)["paths"]["/x"]["get"]["tags"] == ["Samples"]


class IngestIsSuperuserOnly(TestCase):
    def setUp(self):
        self.plain = User.objects.create_user("plain-member", password="p")
        self.admin = User.objects.create_superuser("site-admin", password="p")
        self.client = APIClient()

    def _post(self, user, path, body):
        self.client.force_authenticate(user=user)
        with patch.object(schema_processor.requests, "get") as fetched, \
             patch("nextseek_api.services.schema_rag.schema_rag_service.ingest_schema") as ingest, \
             patch("nextseek_api.services.schema_rag.schema_rag_service.retrieve_endpoints") as retrieve:
            ingest.return_value.success = False
            ingest.return_value.error.error_code = "SCHEMA_FETCH_FAILED"
            ingest.return_value.error.message = "m"
            ingest.return_value.error.details = {}
            retrieve.return_value.model_dump.return_value = {"session_id": "s", "mode": "minimal"}
            resp = self.client.post(path, body, format="json")
        return resp, fetched, ingest, retrieve

    def test_a_non_superuser_may_not_ingest(self):
        resp, fetched, ingest, _ = self._post(self.plain, INGEST, {"schema_url": FOREIGN})
        assert resp.status_code == 403
        ingest.assert_not_called()
        fetched.assert_not_called()

    def test_the_ci_registry_expects_the_sweep_account_to_be_refused(self):
        """ci/routes.py declares the answer the post-deploy sweep's account, a non-superuser, gets here."""
        # Loaded by path: a second package named ci (startup/ci) can win `import ci` on a pytest-xdist
        # worker that ran the startup tests first, and `from ci.routes import ...` then fails.
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location("ci_routes_by_path", __import__("pathlib").Path(__file__).resolve().parents[3] / "ci" / "routes.py")
        routes = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = routes
        spec.loader.exec_module(routes)
        REGISTRY = routes.REGISTRY
        entry = next(r for r in REGISTRY if r.path == INGEST)
        resp, _, _, _ = self._post(self.plain, INGEST, {"schema_url": FOREIGN})
        assert (entry.auth, entry.expect) == ("smoke", resp.status_code)

    def test_a_superuser_may_ingest(self):
        resp, _, ingest, _ = self._post(self.admin, INGEST, {"schema_url": FOREIGN})
        assert resp.status_code == 502  # reached the (stubbed) ingest, which failed
        ingest.assert_called_once()

    def test_a_non_superuser_may_not_retrieve_by_a_foreign_schema_url(self):
        resp, fetched, _, retrieve = self._post(
            self.plain, RETRIEVE, {"schema_url": FOREIGN, "query": "q"})
        assert resp.status_code == 403
        retrieve.assert_not_called()
        fetched.assert_not_called()

    def test_a_non_superuser_may_retrieve_by_session_id(self):
        resp, _, _, retrieve = self._post(self.plain, RETRIEVE, {"session_id": "abc", "query": "q"})
        assert resp.status_code == 200
        retrieve.assert_called_once()

    def test_a_non_superuser_may_retrieve_by_this_instances_own_schema(self):
        with patch("NessieAI.schema_rag.schema_processor.is_self_schema_url", return_value=True):
            resp, _, _, retrieve = self._post(
                self.plain, RETRIEVE, {"schema_url": "https://nextseek.example.org/nextseek_api/schema/",
                                       "query": "q"})
        assert resp.status_code == 200
        retrieve.assert_called_once()

    def test_a_superuser_may_retrieve_by_a_foreign_schema_url(self):
        resp, _, _, retrieve = self._post(self.admin, RETRIEVE, {"schema_url": FOREIGN, "query": "q"})
        assert resp.status_code == 200
        retrieve.assert_called_once()
