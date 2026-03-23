"""Coverage tests for schema_rag/schema_processor.py — targeting uncovered lines.

Covers:
- fetch_schema: HTTP error, parse error (JSON/YAML), $ref resolution error, exception
- _convert_jsonref_to_dict: circular reference, list handling
- simplify_schema: allOf merge, anyOf/oneOf, arrays, enum, unknown type
- _ensure_dict_schema: list and string inputs
- extract_request_schema: fallback content type, missing request body
- extract_response_schema: no responses, fallback content type
- extract_parameters: non-dict param, missing name
- extract_and_dehydrate_examples: singular example (non-dict), examples with non-dict value
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from nextseek_api.schema_rag.schema_processor import OpenAPISchemaProcessor
from nextseek_api.schema_rag.errors import SchemaFetchError


class TestFetchSchema:

    @patch("nextseek_api.schema_rag.schema_processor.requests.get")
    def test_http_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("timeout")
        processor = OpenAPISchemaProcessor()
        with pytest.raises(SchemaFetchError, match="HTTP request failed"):
            processor.fetch_schema("http://example.com/schema.yaml")

    @patch("nextseek_api.schema_rag.schema_processor.requests.get")
    def test_generic_exception(self, mock_get):
        mock_get.side_effect = RuntimeError("unexpected")
        processor = OpenAPISchemaProcessor()
        with pytest.raises(SchemaFetchError, match="HTTP request failed"):
            processor.fetch_schema("http://example.com/schema.yaml")

    @patch("nextseek_api.schema_rag.schema_processor.requests.get")
    def test_json_parse_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.text = "not valid json {{{"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        processor = OpenAPISchemaProcessor()
        with pytest.raises(SchemaFetchError, match="parsing failed"):
            processor.fetch_schema("http://example.com/schema.json")

    @patch("nextseek_api.schema_rag.schema_processor.jsonref.replace_refs")
    @patch("nextseek_api.schema_rag.schema_processor.requests.get")
    def test_ref_resolution_error(self, mock_get, mock_replace):
        import jsonref
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "application/yaml"}
        mock_response.text = "openapi: 3.0.0\npaths: {}\n"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        mock_replace.side_effect = RecursionError("too deep")
        processor = OpenAPISchemaProcessor()
        with pytest.raises(SchemaFetchError, match="ref resolution failed"):
            processor.fetch_schema("http://example.com/schema.yaml")


class TestConvertJsonrefToDict:

    def test_circular_reference(self):
        processor = OpenAPISchemaProcessor()
        # Create a circular dict
        d = {"key": "value"}
        d["self"] = d  # circular
        result = processor._convert_jsonref_to_dict(d)
        # The circular reference should be broken with {}
        assert result["key"] == "value"
        assert result["self"] == {}

    def test_list_handling(self):
        processor = OpenAPISchemaProcessor()
        result = processor._convert_jsonref_to_dict([{"a": 1}, "b", 3])
        assert result == [{"a": 1}, "b", 3]

    def test_primitive(self):
        processor = OpenAPISchemaProcessor()
        assert processor._convert_jsonref_to_dict(42) == 42
        assert processor._convert_jsonref_to_dict("text") == "text"


class TestSimplifySchema:

    def test_allof_merge(self):
        processor = OpenAPISchemaProcessor()
        schema = {
            "allOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
                {"type": "object", "properties": {"b": {"type": "integer"}}},
            ]
        }
        result = processor.simplify_schema(schema)
        assert "a" in result
        assert "b" in result

    def test_anyof_single(self):
        processor = OpenAPISchemaProcessor()
        schema = {"anyOf": [{"type": "string"}]}
        result = processor.simplify_schema(schema)
        assert result == "string"

    def test_anyof_multiple(self):
        processor = OpenAPISchemaProcessor()
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        result = processor.simplify_schema(schema)
        assert "$anyOf" in result

    def test_array_with_items(self):
        processor = OpenAPISchemaProcessor()
        schema = {"type": "array", "items": {"type": "string"}}
        result = processor.simplify_schema(schema)
        assert result == ["string"]

    def test_array_without_items(self):
        processor = OpenAPISchemaProcessor()
        schema = {"type": "array"}
        result = processor.simplify_schema(schema)
        assert result == ["any"]

    def test_enum_values(self):
        processor = OpenAPISchemaProcessor()
        schema = {"type": "string", "enum": ["A", "B", "C"]}
        result = processor.simplify_schema(schema)
        assert result == {"type": "string", "enum": ["A", "B", "C"]}

    def test_unknown_type(self):
        processor = OpenAPISchemaProcessor()
        schema = {"type": "customType"}
        result = processor.simplify_schema(schema)
        assert result == "customType"

    def test_no_type(self):
        processor = OpenAPISchemaProcessor()
        schema = {"description": "something"}
        result = processor.simplify_schema(schema)
        assert result == "any"

    def test_none_input(self):
        processor = OpenAPISchemaProcessor()
        assert processor.simplify_schema(None) == {}

    def test_object_no_properties(self):
        processor = OpenAPISchemaProcessor()
        schema = {"type": "object"}
        result = processor.simplify_schema(schema)
        assert result == {"type": "object"}


class TestEnsureDictSchema:

    def test_dict_passthrough(self):
        processor = OpenAPISchemaProcessor()
        assert processor._ensure_dict_schema({"a": 1}) == {"a": 1}

    def test_list_wrapped(self):
        processor = OpenAPISchemaProcessor()
        result = processor._ensure_dict_schema(["string"])
        assert result == {"$items": ["string"]}

    def test_string_wrapped(self):
        processor = OpenAPISchemaProcessor()
        result = processor._ensure_dict_schema("string")
        assert result == {"type": "string"}

    def test_other_returns_empty(self):
        processor = OpenAPISchemaProcessor()
        assert processor._ensure_dict_schema(42) == {}


class TestExtractRequestSchema:

    def test_missing_request_body(self):
        processor = OpenAPISchemaProcessor()
        result = processor.extract_request_schema({"description": "test"})
        assert result == {}

    def test_fallback_content_type(self):
        processor = OpenAPISchemaProcessor()
        operation = {
            "requestBody": {
                "content": {
                    "text/xml": {
                        "schema": {"type": "string"}
                    }
                }
            }
        }
        result = processor.extract_request_schema(operation)
        assert result == {"type": "string"}


class TestExtractResponseSchema:

    def test_no_responses(self):
        processor = OpenAPISchemaProcessor()
        result = processor.extract_response_schema({"description": "test"})
        assert result is None

    def test_fallback_content_type(self):
        processor = OpenAPISchemaProcessor()
        operation = {
            "responses": {
                "200": {
                    "content": {
                        "text/xml": {
                            "schema": {"type": "string"}
                        }
                    }
                }
            }
        }
        result = processor.extract_response_schema(operation)
        assert result == {"type": "string"}


class TestExtractParameters:

    def test_non_dict_param_skipped(self):
        processor = OpenAPISchemaProcessor()
        result = processor.extract_parameters({"parameters": ["not-a-dict", {"name": "id", "in": "path"}]})
        assert "id" in result

    def test_missing_name_skipped(self):
        processor = OpenAPISchemaProcessor()
        result = processor.extract_parameters({"parameters": [{"in": "query"}]})
        assert result is None

    def test_non_dict_schema(self):
        processor = OpenAPISchemaProcessor()
        result = processor.extract_parameters({
            "parameters": [{"name": "q", "in": "query", "schema": "string"}]
        })
        assert result["q"]["type"] == "string"


class TestExtractAndDehydrateExamples:

    def test_singular_example_dict(self):
        processor = OpenAPISchemaProcessor()
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "example": {"key": "value"}
                    }
                }
            }
        }
        result = processor.extract_and_dehydrate_examples(operation)
        assert result is not None
        assert '{"key":"value"}' in result[0]

    def test_singular_example_string(self):
        processor = OpenAPISchemaProcessor()
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "example": "plain text example"
                    }
                }
            }
        }
        result = processor.extract_and_dehydrate_examples(operation)
        assert result == ["plain text example"]

    def test_multiple_examples(self):
        processor = OpenAPISchemaProcessor()
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "examples": {
                            "ex1": {"value": {"a": 1}},
                            "ex2": {"value": "text"},
                        }
                    }
                }
            }
        }
        result = processor.extract_and_dehydrate_examples(operation)
        assert len(result) == 2

    def test_no_examples(self):
        processor = OpenAPISchemaProcessor()
        result = processor.extract_and_dehydrate_examples({"description": "test"})
        assert result is None

    def test_non_dict_content_obj_skipped(self):
        processor = OpenAPISchemaProcessor()
        operation = {
            "requestBody": {
                "content": {
                    "application/json": "not-a-dict"
                }
            }
        }
        result = processor.extract_and_dehydrate_examples(operation)
        assert result is None
