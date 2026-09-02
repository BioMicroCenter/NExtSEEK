"""Tests for /nextseek_api/templates/ -- the Download Templates tool as an API."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.services.template_catalog import SampleTypeEntry

CATALOG_URL = "/nextseek_api/templates/catalog/"

_TIS = SampleTypeEntry(code="TIS", sample_type_id=2, name="Tissue",
                       description="A tissue sample.", group="")
_DNA = SampleTypeEntry(code="DNA", sample_type_id=42, name="DNA",
                       description="Extracted DNA.", group="")
_SEQ = SampleTypeEntry(code="D.SEQ", sample_type_id=11, name="Sequencing Data",
                       description="Reads.", group="D.")

_PAYLOAD = {
    "groups": [
        {"key": "", "label": "Experimental types", "entries": [_DNA, _TIS]},
        {"key": "D.", "label": "Data types", "entries": [_SEQ]},
    ],
    "children": {"TIS": ["DNA"]},
    "requires": {"D.SEQ": {"add": ["DNA"], "assays": ["Whole Genome Sequencing"]}},
    "companions": {"TIS": {"add": ["D.SEQ"], "assays": []}},
    "max_suggestions": 12,
}


def _patch_catalog(testcase, payload=_PAYLOAD):
    patcher = patch("nextseek_api.services.templates.build_catalog",
                    return_value=payload)
    patcher.start()
    testcase.addCleanup(patcher.stop)


class CatalogAuthTests(TestCase):
    databases = {"default"}

    def test_anonymous_is_rejected(self):
        self.assertEqual(APIClient().get(CATALOG_URL).status_code, 401)

    def test_any_authenticated_user_may_read_the_catalog(self):
        # Deliberate, and pinned so that tightening it later has to be a
        # conscious change: the catalog is schema, not sample data, and
        # seek.views.assets.templatesList already serves exactly this to any
        # logged-in user with no project-membership check.
        _patch_catalog(self)
        client = APIClient()
        client.force_authenticate(user=User.objects.create_user("plain", password="pw"))
        self.assertEqual(client.get(CATALOG_URL).status_code, 200)


class CatalogContentTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            user=User.objects.create_user("plain", password="pw")
        )
        _patch_catalog(self)

    def test_groups_keep_their_order_and_entries(self):
        body = self.client.get(CATALOG_URL).json()
        self.assertEqual([g["key"] for g in body["groups"]], ["", "D."])
        self.assertEqual([g["label"] for g in body["groups"]],
                         ["Experimental types", "Data types"])
        self.assertEqual([e["code"] for e in body["groups"][0]["entries"]],
                         ["DNA", "TIS"])

    def test_entries_are_serialised_in_full(self):
        body = self.client.get(CATALOG_URL).json()
        self.assertEqual(
            body["groups"][0]["entries"][0],
            {"code": "DNA", "sample_type_id": 42, "name": "DNA",
             "description": "Extracted DNA.", "group": ""},
        )

    def test_relations_are_passed_through(self):
        body = self.client.get(CATALOG_URL).json()
        self.assertEqual(body["children"], {"TIS": ["DNA"]})
        self.assertEqual(
            body["requires"],
            {"D.SEQ": {"add": ["DNA"], "assays": ["Whole Genome Sequencing"]}},
        )
        self.assertEqual(body["companions"],
                         {"TIS": {"add": ["D.SEQ"], "assays": []}})
        self.assertEqual(body["max_suggestions"], 12)


class CatalogEmptyTableTests(TestCase):
    databases = {"default"}

    def test_an_unpopulated_requirements_table_is_not_an_error(self):
        # The table ships empty and the picker treats that as "nothing known",
        # so a fresh install must get 200 with empty relations, never a 500.
        empty = dict(_PAYLOAD, requires={}, companions={})
        _patch_catalog(self, empty)
        client = APIClient()
        client.force_authenticate(user=User.objects.create_user("plain", password="pw"))
        resp = client.get(CATALOG_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["requires"], {})
        self.assertEqual(resp.json()["companions"], {})
        self.assertTrue(resp.json()["groups"])


class CatalogSchemaTests(TestCase):
    databases = {"default"}

    def test_the_route_is_registered(self):
        from django.urls import reverse

        self.assertTrue(reverse("nextseek_api:templates-catalog").endswith(
            "/templates/catalog/"))

    def test_the_path_and_models_appear_in_the_openapi_schema(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        self.assertIn("/nextseek_api/templates/catalog/", schema["paths"])
        operation = schema["paths"]["/nextseek_api/templates/catalog/"]["get"]
        self.assertEqual(operation["operationId"], "Templates: Catalog (GET)")
        self.assertIn("templates", operation["tags"])
        examples = operation["responses"]["200"]["content"]["application/json"]["examples"]
        self.assertGreaterEqual(len(examples), 1)
        self.assertIn("TemplateCatalogResponse", schema["components"]["schemas"])
