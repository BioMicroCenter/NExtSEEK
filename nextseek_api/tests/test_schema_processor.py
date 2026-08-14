"""
Unit tests for OpenAPISchemaProcessor.

Tests cover:
- Schema simplification (required preservation, enum preservation, allOf merging)
- Schema fetching (JSON parsing, YAML parsing, $ref resolution, error handling)
- Schema extraction (request schema, response schema)

These tests are designed to run before the implementation (TDD approach).
"""

import json
from unittest.mock import Mock, patch

from django.test import TestCase

from nextseek_api.schema_rag.errors import SchemaFetchError
from nextseek_api.schema_rag.schema_processor import OpenAPISchemaProcessor


# ---------------------------------------------------------------------------
# Test Fixtures - OpenAPI Documents
# ---------------------------------------------------------------------------

# Schema with $ref that should be resolved
OPENAPI_WITH_REFS = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/studies": {
            "post": {
                "operationId": "createStudy",
                "summary": "Create a study",
                "tags": ["Studies"],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/StudyCreate"}
                        }
                    }
                },
                "responses": {"201": {"description": "Created"}}
            }
        }
    },
    "components": {
        "schemas": {
            "StudyCreate": {
                "type": "object",
                "required": ["title", "investigation_id"],
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "investigation_id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["draft", "active", "archived"]}
                }
            }
        }
    }
}

# Schema with allOf that should be merged
OPENAPI_WITH_ALLOF = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/items": {
            "post": {
                "operationId": "createItem",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "allOf": [
                                    {"$ref": "#/components/schemas/BaseItem"},
                                    {"$ref": "#/components/schemas/ItemExtras"}
                                ]
                            }
                        }
                    }
                },
                "responses": {"201": {"description": "Created"}}
            }
        }
    },
    "components": {
        "schemas": {
            "BaseItem": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "created_at": {"type": "string"}}
            },
            "ItemExtras": {
                "type": "object",
                "required": ["category"],
                "properties": {"category": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}
            }
        }
    }
}


# ---------------------------------------------------------------------------
# Test Class
# ---------------------------------------------------------------------------


class TestOpenAPISchemaProcessor(TestCase):
    """Unit tests for OpenAPISchemaProcessor."""

    def setUp(self):
        """Set up test processor instance."""
        self.processor = OpenAPISchemaProcessor()

    # -------------------------------------------------------------------------
    # Simplification Tests (no mocking needed)
    # -------------------------------------------------------------------------

    def test_simplify_preserves_required(self):
        """Test that required arrays are preserved as $required."""
        schema = {
            "type": "object",
            "required": ["name", "email"],
            "properties": {"name": {"type": "string"}, "email": {"type": "string"}}
        }
        result = self.processor.simplify_schema(schema)
        self.assertIn("$required", result)
        self.assertEqual(set(result["$required"]), {"name", "email"})
        self.assertEqual(result["name"], "string")

    def test_simplify_preserves_enum(self):
        """Test that enum values are preserved."""
        schema = {"type": "string", "enum": ["draft", "active", "archived"]}
        result = self.processor.simplify_schema(schema)
        self.assertEqual(result, {"type": "string", "enum": ["draft", "active", "archived"]})

    def test_simplify_merges_allof(self):
        """Test that allOf schemas are merged, not just first taken."""
        schema = {
            "allOf": [
                {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}},
                {"type": "object", "required": ["b"], "properties": {"b": {"type": "integer"}}}
            ]
        }
        result = self.processor.simplify_schema(schema)
        self.assertIn("a", result)
        self.assertIn("b", result)
        self.assertIn("$required", result)
        self.assertIn("a", result["$required"])
        self.assertIn("b", result["$required"])

    def test_simplify_nested_objects(self):
        """Test that nested object structure is preserved."""
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}
                }
            }
        }
        result = self.processor.simplify_schema(schema)
        self.assertIn("user", result)
        self.assertEqual(result["user"]["name"], "string")
        self.assertEqual(result["user"]["age"], "integer")

    def test_simplify_arrays(self):
        """Test that arrays are simplified to [element_type]."""
        schema = {"type": "array", "items": {"type": "string"}}
        result = self.processor.simplify_schema(schema)
        self.assertEqual(result, ["string"])

    def test_simplify_basic_types(self):
        """Test that basic types without enum return just the type string."""
        self.assertEqual(self.processor.simplify_schema({"type": "string"}), "string")
        self.assertEqual(self.processor.simplify_schema({"type": "integer"}), "integer")
        self.assertEqual(self.processor.simplify_schema({"type": "number"}), "number")
        self.assertEqual(self.processor.simplify_schema({"type": "boolean"}), "boolean")

    def test_simplify_anyof_takes_first(self):
        """Test that anyOf with multiple variants returns $anyOf wrapper."""
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "integer"}
            ]
        }
        result = self.processor.simplify_schema(schema)
        self.assertEqual(result, {"$anyOf": ["string", "integer"]})

    def test_simplify_oneof_takes_first(self):
        """Test that oneOf with multiple variants returns $oneOf wrapper."""
        schema = {
            "oneOf": [
                {"type": "boolean"},
                {"type": "string"}
            ]
        }
        result = self.processor.simplify_schema(schema)
        self.assertEqual(result, {"$oneOf": ["boolean", "string"]})

    def test_simplify_empty_object(self):
        """Test that object without properties returns type indicator."""
        schema = {"type": "object"}
        result = self.processor.simplify_schema(schema)
        self.assertEqual(result, {"type": "object"})

    def test_simplify_none_returns_empty(self):
        """Test that None input returns empty dict."""
        result = self.processor.simplify_schema(None)
        self.assertEqual(result, {})

    # -------------------------------------------------------------------------
    # Fetch Tests (with mocking)
    # -------------------------------------------------------------------------

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_fetch_resolves_refs(self, mock_get):
        """Test that $ref pointers are resolved after fetching."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(OPENAPI_WITH_REFS)
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = self.processor.fetch_schema("https://example.com/api.json")

        # The $ref should be resolved to actual schema content
        request_schema = result["paths"]["/studies"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertIn("properties", request_schema)
        self.assertIn("title", request_schema["properties"])
        self.assertNotIn("$ref", request_schema)

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_fetch_handles_yaml(self, mock_get):
        """Test that YAML content is parsed correctly."""
        yaml_content = "openapi: '3.0.0'\ninfo:\n  title: Test\n  version: '1.0'"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = yaml_content
        mock_response.headers = {"Content-Type": "application/x-yaml"}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = self.processor.fetch_schema("https://example.com/api.yaml")
        self.assertEqual(result["openapi"], "3.0.0")

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_fetch_handles_json(self, mock_get):
        """Test that JSON content is parsed correctly."""
        json_content = '{"openapi": "3.0.0", "info": {"title": "Test", "version": "1.0"}}'
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json_content
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = self.processor.fetch_schema("https://example.com/api.json")
        self.assertEqual(result["openapi"], "3.0.0")

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_fetch_error_handling(self, mock_get):
        """Test that SchemaFetchError is raised on HTTP errors."""
        mock_get.side_effect = Exception("Connection refused")

        with self.assertRaises(SchemaFetchError):
            self.processor.fetch_schema("https://example.com/api.json")

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_fetch_resolves_allof_refs(self, mock_get):
        """Test that allOf with $ref pointers are all resolved."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(OPENAPI_WITH_ALLOF)
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = self.processor.fetch_schema("https://example.com/api.json")

        # The allOf refs should be resolved
        allof_schema = result["paths"]["/items"]["post"]["requestBody"]["content"]["application/json"]["schema"]["allOf"]
        # Each item in allOf should now have resolved properties
        self.assertIn("properties", allof_schema[0])
        self.assertIn("name", allof_schema[0]["properties"])
        self.assertIn("properties", allof_schema[1])
        self.assertIn("category", allof_schema[1]["properties"])

    # -------------------------------------------------------------------------
    # Extract Tests
    # -------------------------------------------------------------------------

    def test_extract_request_schema_with_resolved_ref(self):
        """Test extracting and simplifying request schema after ref resolution."""
        # Simulate already-resolved schema (what fetch_schema returns)
        resolved_operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["title"],
                            "properties": {
                                "title": {"type": "string"},
                                "status": {"type": "string", "enum": ["draft", "active"]}
                            }
                        }
                    }
                }
            }
        }
        result = self.processor.extract_request_schema(resolved_operation)
        self.assertIn("title", result)
        self.assertIn("$required", result)
        self.assertIn("status", result)
        self.assertIn("enum", result["status"])

    def test_extract_request_schema_empty_body(self):
        """Test that missing request body returns empty dict."""
        operation = {"responses": {"200": {"description": "OK"}}}
        result = self.processor.extract_request_schema(operation)
        self.assertEqual(result, {})

    def test_extract_response_schema(self):
        """Test extracting and simplifying response schema."""
        operation = {
            "responses": {
                "200": {
                    "description": "Success",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "integer"},
                                    "name": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
        result = self.processor.extract_response_schema(operation)
        self.assertIn("id", result)
        self.assertIn("name", result)

    def test_extract_response_schema_none(self):
        """Test that missing responses returns None."""
        operation = {"operationId": "test"}
        result = self.processor.extract_response_schema(operation)
        self.assertIsNone(result)

    def test_extract_parameters(self):
        """Test extracting parameters from operation."""
        operation = {
            "parameters": [
                {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                {"name": "filter", "in": "query", "required": False, "schema": {"type": "string"}}
            ]
        }
        result = self.processor.extract_parameters(operation)
        self.assertIn("id", result)
        self.assertEqual(result["id"]["in"], "path")
        self.assertEqual(result["id"]["required"], True)
        self.assertEqual(result["id"]["type"], "integer")
        self.assertIn("filter", result)
        self.assertEqual(result["filter"]["in"], "query")
        self.assertEqual(result["filter"]["required"], False)

    def test_extract_parameters_none(self):
        """Test that missing parameters returns None."""
        operation = {"operationId": "test"}
        result = self.processor.extract_parameters(operation)
        self.assertIsNone(result)

    def test_extract_and_dehydrate_examples(self):
        """Test extracting and dehydrating examples."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "example": {"title": "Test", "count": 5}
                    }
                }
            }
        }
        result = self.processor.extract_and_dehydrate_examples(operation)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        # Should be JSON string
        self.assertIn("title", result[0])
        self.assertIn("Test", result[0])

    def test_extract_and_dehydrate_examples_multiple(self):
        """Test extracting multiple examples."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "examples": {
                            "example1": {"value": {"name": "First"}},
                            "example2": {"value": {"name": "Second"}}
                        }
                    }
                }
            }
        }
        result = self.processor.extract_and_dehydrate_examples(operation)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    def test_extract_and_dehydrate_examples_none(self):
        """Test that missing examples returns None."""
        operation = {"operationId": "test"}
        result = self.processor.extract_and_dehydrate_examples(operation)
        self.assertIsNone(result)

    # -------------------------------------------------------------------------
    # Integration-style tests (combining fetch + simplify)
    # -------------------------------------------------------------------------

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_full_pipeline_ref_resolution_and_simplification(self, mock_get):
        """Test the full pipeline: fetch with $ref resolution, then extract and simplify."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(OPENAPI_WITH_REFS)
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Fetch and resolve
        openapi_doc = self.processor.fetch_schema("https://example.com/api.json")

        # Extract operation
        operation = openapi_doc["paths"]["/studies"]["post"]

        # Extract and simplify request schema
        result = self.processor.extract_request_schema(operation)

        # Verify the full pipeline worked
        self.assertIn("title", result)
        self.assertIn("description", result)
        self.assertIn("investigation_id", result)
        self.assertIn("status", result)
        self.assertIn("$required", result)
        self.assertIn("title", result["$required"])
        self.assertIn("investigation_id", result["$required"])
        # Status should have enum preserved
        self.assertIn("enum", result["status"])
        self.assertEqual(result["status"]["enum"], ["draft", "active", "archived"])

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_full_pipeline_allof_merge(self, mock_get):
        """Test the full pipeline with allOf merging."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(OPENAPI_WITH_ALLOF)
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Fetch and resolve
        openapi_doc = self.processor.fetch_schema("https://example.com/api.json")

        # Extract operation
        operation = openapi_doc["paths"]["/items"]["post"]

        # Extract and simplify request schema
        result = self.processor.extract_request_schema(operation)

        # Verify allOf was merged
        self.assertIn("name", result)       # from BaseItem
        self.assertIn("created_at", result)  # from BaseItem
        self.assertIn("category", result)    # from ItemExtras
        self.assertIn("tags", result)        # from ItemExtras
        self.assertIn("$required", result)
        self.assertIn("name", result["$required"])
        self.assertIn("category", result["$required"])



# ---------------------------------------------------------------------------
# Internal-URL rewrite for self-ingest (#19)
# ---------------------------------------------------------------------------

import os
from unittest.mock import patch as _patch

from django.test import override_settings

INTERNAL = "http://127.0.0.1:8000"
PUBLIC_HOST = "nextseek-dev.example.mit.edu"


def _resolve():
    """Imported lazily so this module still collects against a tree without the
    helper (the pre-fix state), letting the behavioural tests below report the
    real failure instead of a collection error."""
    from nextseek_api.schema_rag.schema_processor import resolve_transport_url

    return resolve_transport_url


def _env(**overrides):
    """Env with all base-URL keys cleared, then the given overrides applied."""
    base = {
        "NEXTSEEK_INTERNAL_BASE_URL": "",
        "NEXTSEEK_BASE_URL": "",
        "NEXTSEEK_HOSTNAME": "",
        "NEXTSEEK_PROD_URL": "",
    }
    base.update(overrides)
    return _patch.dict(os.environ, base, clear=False)


@override_settings(ALLOWED_HOSTS=[PUBLIC_HOST])
class TestResolveTransportUrl(TestCase):
    """The container cannot resolve its own public FQDN, so ingesting our own
    OpenAPI schema by its public URL died with SCHEMA_FETCH_FAILED. Rewrite
    only OUR host to the internal base; leave everything else alone.
    """

    def test_own_public_host_is_rewritten_to_internal(self):
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            got = _resolve()(f"https://{PUBLIC_HOST}/nextseek_api/schema/")
        self.assertEqual(got, f"{INTERNAL}/nextseek_api/schema/")

    def test_query_and_fragment_are_preserved(self):
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            got = _resolve()(
                f"https://{PUBLIC_HOST}/nextseek_api/schema/?format=json#top"
            )
        self.assertEqual(got, f"{INTERNAL}/nextseek_api/schema/?format=json#top")

    def test_public_host_from_nextseek_base_url_env(self):
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL,
                  NEXTSEEK_BASE_URL="http://ns.example.org:9001"):
            got = _resolve()("http://ns.example.org:9001/x.json")
        self.assertEqual(got, f"{INTERNAL}/x.json")

    def test_public_host_from_nextseek_hostname_env(self):
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL,
                  NEXTSEEK_HOSTNAME="ns2.example.org:9002"):
            got = _resolve()("http://ns2.example.org:9002/x.json")
        self.assertEqual(got, f"{INTERNAL}/x.json")

    def test_a_bumped_public_port_still_matches_on_host(self):
        """The published port is auto-bumped when 8000 is busy; matching is on
        hostname alone so the rewrite still fires."""
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL,
                  NEXTSEEK_BASE_URL="http://ns.example.org:8000"):
            got = _resolve()("http://ns.example.org:8123/x.json")
        self.assertEqual(got, f"{INTERNAL}/x.json")

    # --- the additive half: everything else must be untouched -------------

    def test_unrelated_external_url_is_untouched(self):
        url = "https://petstore3.swagger.io/api/v3/openapi.json"
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            self.assertEqual(_resolve()(url), url)

    def test_no_internal_base_configured_is_a_noop(self):
        url = f"https://{PUBLIC_HOST}/nextseek_api/schema/"
        with _env():
            self.assertEqual(_resolve()(url), url)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_wildcard_allowed_hosts_does_not_capture_the_world(self):
        """ALLOWED_HOSTS=['*'] is common in dev; it must not mean 'every URL
        is ours'."""
        url = "https://petstore3.swagger.io/api/v3/openapi.json"
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            self.assertEqual(_resolve()(url), url)

    def test_non_url_input_is_untouched(self):
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            self.assertEqual(_resolve()("/local/path.json"), "/local/path.json")


@override_settings(ALLOWED_HOSTS=[PUBLIC_HOST])
class TestFetchSchemaUsesInternalUrl(TestCase):
    """fetch_schema must GET the rewritten URL."""

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_fetch_schema_gets_the_internal_url(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"openapi": "3.0.0", "info": {"title": "T", "version": "1"}}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            OpenAPISchemaProcessor().fetch_schema(
                f"https://{PUBLIC_HOST}/nextseek_api/schema/"
            )

        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, f"{INTERNAL}/nextseek_api/schema/")

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_fetch_schema_leaves_external_urls_alone(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"openapi": "3.0.0", "info": {"title": "T", "version": "1"}}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        url = "https://petstore3.swagger.io/api/v3/openapi.json"
        with _env(NEXTSEEK_INTERNAL_BASE_URL=INTERNAL):
            OpenAPISchemaProcessor().fetch_schema(url)

        self.assertEqual(mock_get.call_args[0][0], url)
