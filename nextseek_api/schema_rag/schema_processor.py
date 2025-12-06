"""
OpenAPI schema processor for Schema RAG.

Handles fetching, parsing, reference resolution, and simplification of OpenAPI schemas.

This module provides:
- HTTP fetching with YAML/JSON parsing
- $ref resolution using jsonref
- Schema simplification (preserving required, enum, merging allOf)
- Request/response schema extraction
- Parameter and example extraction
"""

import json
import logging
from typing import Any, Dict, List, Optional

import jsonref
import requests
import yaml

from .errors import SchemaFetchError

logger = logging.getLogger(__name__)


class OpenAPISchemaProcessor:
    """
    Fetches, resolves, and simplifies OpenAPI schemas.

    This class encapsulates all schema processing logic:
    - HTTP fetching with YAML/JSON parsing
    - $ref resolution using jsonref
    - Schema simplification (preserving required, enum, merging allOf)
    - Request/response schema extraction
    """

    def __init__(self, timeout: int = 30):
        """
        Initialize processor with configuration.

        Args:
            timeout: HTTP request timeout in seconds.
        """
        self.timeout = timeout

    def fetch_schema(self, schema_url: str) -> dict:
        """
        Fetch OpenAPI schema from URL, parse, and resolve all $ref pointers.

        Args:
            schema_url: URL of the OpenAPI schema.

        Returns:
            Parsed OpenAPI document with all $ref resolved.

        Raises:
            SchemaFetchError: If fetch, parse, or resolution fails.
        """
        # Step 1: HTTP GET
        try:
            response = requests.get(schema_url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error("Failed to fetch schema from %s: %s", schema_url, e)
            raise SchemaFetchError(f"HTTP request failed: {e}") from e
        except Exception as e:
            logger.error("Failed to fetch schema from %s: %s", schema_url, e)
            raise SchemaFetchError(f"HTTP request failed: {e}") from e

        # Step 2: Parse YAML or JSON
        content_type = response.headers.get("Content-Type", "")
        content = response.text

        try:
            if "json" in content_type.lower() or content.strip().startswith("{"):
                parsed = json.loads(content)
            else:
                parsed = yaml.safe_load(content)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            logger.error("Failed to parse schema from %s: %s", schema_url, e)
            raise SchemaFetchError(f"Schema parsing failed: {e}") from e

        # Step 3: Resolve all $ref pointers
        try:
            resolved = jsonref.replace_refs(parsed)
            # Convert back to regular dict (jsonref returns proxy objects)
            # Use recursive conversion to handle circular references
            return self._convert_jsonref_to_dict(resolved)
        except (jsonref.JsonRefError, RecursionError) as e:
            logger.error("Failed to resolve $ref in schema from %s: %s", schema_url, e)
            raise SchemaFetchError(f"$ref resolution failed: {e}") from e

    def _convert_jsonref_to_dict(self, obj: Any, _ancestors: Optional[set] = None) -> Any:
        """
        Recursively convert jsonref proxy objects to regular Python dicts.
        
        Handles circular references by tracking ancestors in the current recursion path.
        Shared references (same object via different paths) are converted normally.
        
        Args:
            obj: Object to convert (may be jsonref proxy, dict, list, or primitive).
            _ancestors: Set of object ids in the current recursion path (for cycle detection).
        
        Returns:
            Plain Python object (dict, list, or primitive).
        """
        if _ancestors is None:
            _ancestors = set()
        
        obj_id = id(obj)
        
        # Only circular if we're descending INTO this same object (ancestor check)
        if obj_id in _ancestors:
            return {}  # Break genuine cycle
        
        if isinstance(obj, dict):
            _ancestors.add(obj_id)
            try:
                return {k: self._convert_jsonref_to_dict(v, _ancestors) for k, v in obj.items()}
            finally:
                _ancestors.discard(obj_id)  # Remove when leaving this branch
        elif isinstance(obj, list):
            return [self._convert_jsonref_to_dict(item, _ancestors) for item in obj]
        else:
            return obj

    def simplify_schema(self, schema: Optional[dict]) -> Any:
        """
        Simplify JSON Schema to field names + basic types, preserving structure.

        Preserves:
        - Nested object/array structure
        - required arrays (as $required key)
        - enum values (as {"type": "...", "enum": [...]})

        Merges allOf schemas instead of taking first.

        Args:
            schema: JSON Schema dictionary.

        Returns:
            Simplified schema (dict, list, or string).
        """
        if schema is None or not isinstance(schema, dict):
            return {}

        # Handle allOf - MERGE all schemas
        if "allOf" in schema and isinstance(schema["allOf"], list) and schema["allOf"]:
            merged = {}
            merged_required = []
            for sub_schema in schema["allOf"]:
                simplified_sub = self.simplify_schema(sub_schema)
                if isinstance(simplified_sub, dict):
                    # Collect $required before merging
                    if "$required" in simplified_sub:
                        merged_required.extend(simplified_sub.pop("$required"))
                    merged.update(simplified_sub)
            if merged_required:
                merged["$required"] = list(set(merged_required))
            return merged if merged else {}

        # Handle anyOf, oneOf - take first (unchanged behavior)
        for combiner in ("anyOf", "oneOf"):
            if combiner in schema and isinstance(schema[combiner], list) and schema[combiner]:
                return self.simplify_schema(schema[combiner][0])

        schema_type = schema.get("type")

        # Handle objects with properties
        if schema_type == "object" or "properties" in schema:
            properties = schema.get("properties", {})
            if not properties:
                return {"type": "object"}

            simplified = {}
            for prop_name, prop_schema in properties.items():
                simplified[prop_name] = self.simplify_schema(prop_schema)

            # Preserve required array
            required = schema.get("required", [])
            if required:
                simplified["$required"] = list(required)

            return simplified

        # Handle arrays
        if schema_type == "array":
            items = schema.get("items", {})
            if items:
                return [self.simplify_schema(items)]
            return ["any"]

        # Handle basic types with optional enum
        if schema_type in ("string", "integer", "number", "boolean"):
            enum_values = schema.get("enum")
            if enum_values:
                return {"type": schema_type, "enum": list(enum_values)}
            return schema_type

        # Unknown or missing type
        if schema_type:
            return schema_type

        return "any"

    def _ensure_dict_schema(self, simplified: Any) -> dict:
        """
        Ensure simplified schema is a dict for Pydantic model fields.

        Args:
            simplified: Result from simplify_schema.

        Returns:
            Dictionary representation.
        """
        if isinstance(simplified, dict):
            return simplified
        if isinstance(simplified, list):
            return {"$items": simplified}
        if isinstance(simplified, str):
            return {"type": simplified}
        return {}

    def extract_request_schema(self, operation: dict) -> dict:
        """
        Extract and simplify request body schema from OpenAPI operation.

        Args:
            operation: OpenAPI operation object.

        Returns:
            Simplified request schema dictionary.
        """
        request_body = operation.get("requestBody", {})
        if not request_body:
            return {}

        content = request_body.get("content", {})

        for content_type in ("application/json", "application/vnd.api+json", "*/*"):
            if content_type in content:
                schema = content[content_type].get("schema", {})
                simplified = self.simplify_schema(schema)
                return self._ensure_dict_schema(simplified)

        if content:
            first_content = next(iter(content.values()), {})
            schema = first_content.get("schema", {})
            simplified = self.simplify_schema(schema)
            return self._ensure_dict_schema(simplified)

        return {}

    def extract_response_schema(self, operation: dict) -> Optional[dict]:
        """
        Extract and simplify response schema from OpenAPI operation.

        Args:
            operation: OpenAPI operation object.

        Returns:
            Simplified response schema or None.
        """
        responses = operation.get("responses", {})
        if not responses:
            return None

        for status_code in ("200", "201", "default"):
            if status_code in responses:
                response = responses[status_code]
                content = response.get("content", {})

                for content_type in ("application/json", "application/vnd.api+json", "*/*"):
                    if content_type in content:
                        schema = content[content_type].get("schema", {})
                        simplified = self.simplify_schema(schema)
                        return self._ensure_dict_schema(simplified)

                if content:
                    first_content = next(iter(content.values()), {})
                    schema = first_content.get("schema", {})
                    simplified = self.simplify_schema(schema)
                    return self._ensure_dict_schema(simplified)

        return None

    def extract_parameters(self, operation: dict) -> Optional[dict]:
        """
        Extract and simplify parameters from OpenAPI operation.

        Args:
            operation: OpenAPI operation object.

        Returns:
            Dictionary of parameter_name -> {in, type, required} or None.
        """
        params = operation.get("parameters", [])
        if not params:
            return None

        result = {}
        for param in params:
            if not isinstance(param, dict):
                continue

            name = param.get("name")
            if not name:
                continue

            param_info = {
                "in": param.get("in", "query"),
                "required": param.get("required", False),
            }

            schema = param.get("schema", {})
            if isinstance(schema, dict):
                param_info["type"] = schema.get("type", "string")
            else:
                param_info["type"] = "string"

            result[name] = param_info

        return result if result else None

    def extract_and_dehydrate_examples(self, operation: dict) -> Optional[List[str]]:
        """
        Extract examples from OpenAPI operation and convert to strings.

        Args:
            operation: OpenAPI operation object.

        Returns:
            List of dehydrated example strings or None.
        """
        examples = []

        request_body = operation.get("requestBody", {})
        content = request_body.get("content", {})

        for content_type, content_obj in content.items():
            if not isinstance(content_obj, dict):
                continue

            # Check for 'examples' (multiple)
            content_examples = content_obj.get("examples", {})
            if isinstance(content_examples, dict):
                for example_name, example_obj in content_examples.items():
                    if isinstance(example_obj, dict) and "value" in example_obj:
                        value = example_obj["value"]
                        if isinstance(value, (dict, list)):
                            examples.append(json.dumps(value, separators=(",", ":")))
                        else:
                            examples.append(str(value))

            # Check for 'example' (singular)
            example = content_obj.get("example")
            if example is not None:
                if isinstance(example, (dict, list)):
                    examples.append(json.dumps(example, separators=(",", ":")))
                else:
                    examples.append(str(example))

        return examples if examples else None

