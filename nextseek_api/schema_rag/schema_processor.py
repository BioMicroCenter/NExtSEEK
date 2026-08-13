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
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import jsonref
import requests
import yaml

from .errors import SchemaFetchError

logger = logging.getLogger(__name__)


def _own_public_hosts() -> set:
    """Hostnames (no port) by which this app is known publicly.

    Wildcards are deliberately excluded: ALLOWED_HOSTS is often "*" in dev, and
    treating that as "everything is us" would rewrite unrelated external URLs.
    """
    candidates = []
    for env_key in ("NEXTSEEK_BASE_URL", "NEXTSEEK_HOSTNAME", "NEXTSEEK_PROD_URL"):
        value = os.getenv(env_key)
        if value:
            candidates.append(value)
    try:
        from django.conf import settings

        candidates.extend(settings.ALLOWED_HOSTS or [])
    except Exception:  # settings unconfigured (standalone import) — env only
        pass

    hosts = set()
    for raw in candidates:
        entry = (raw or "").strip()
        if not entry or "*" in entry:
            continue
        # Accept bare hosts, host:port, and full URLs alike.
        if "//" not in entry:
            entry = "//" + entry
        host = urlsplit(entry).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def _own_hosts() -> set:
    """Every hostname that is US: the public ones plus the container-internal one.

    Deliberately separate from `_own_public_hosts()`, which must keep meaning
    exactly "hosts whose URLs need rewriting to the internal base" — folding the
    internal host into it would change `resolve_transport_url`.
    """
    hosts = set(_own_public_hosts())
    internal = (os.getenv("NEXTSEEK_INTERNAL_BASE_URL") or "").strip()
    if internal and "*" not in internal:
        entry = internal if "//" in internal else "//" + internal
        host = urlsplit(entry).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def self_schema_route_path() -> Optional[str]:
    """Path of THIS instance's OpenAPI schema route, or None if unavailable.

    `reverse` is imported lazily and every failure is swallowed because this
    module is importable standalone, with Django unconfigured (same reason
    `_own_public_hosts` guards its `settings` access).
    """
    try:
        from django.urls import reverse

        return reverse("nextseek_api:schema")
    except Exception:  # settings/urls unavailable, or the route was renamed
        return None


def is_self_schema_url(url: str) -> bool:
    """True only for a URL naming OUR OWN OpenAPI schema route.

    BOTH halves are required, and matching on hostname alone would be a
    security bug: `https://<our own host>/anything/else` is still an arbitrary
    fetch of an arbitrary document, and treating it as "ours" would let a
    caller-supplied `schema_url` be answered with our in-process schema — or,
    under any credential-attaching variant of this fix, get our credentials
    attached to a request we never meant to authenticate.
    """
    route = self_schema_route_path()
    if not route:
        return False

    parts = urlsplit(url or "")
    if not parts.hostname:
        return False
    if parts.hostname.lower() not in _own_hosts():
        return False
    return parts.path.rstrip("/") == route.rstrip("/")


def _generate_own_schema() -> Optional[dict]:
    """Build this instance's OpenAPI document in-process, or return None.

    This is exactly what `SpectacularAPIView` serves, minus the HTTP round trip
    to a route we ourselves gate behind `IsAuthenticated` (#77 / #94).

    Returns None — never raises — on ANY failure, so `fetch_schema` can fall
    through to the pre-existing HTTP path and nothing that worked before stops
    working. `drf_spectacular` is imported lazily for the same standalone-import
    reason as `self_schema_route_path`.
    """
    try:
        from drf_spectacular.generators import SchemaGenerator
    except Exception as e:  # drf_spectacular absent or Django unconfigured
        logger.warning(
            "Cannot import drf_spectacular for in-process schema generation "
            "(%s); falling back to HTTP", e
        )
        return None

    try:
        document = SchemaGenerator().get_schema(request=None, public=True)
    except Exception as e:
        logger.warning(
            "In-process OpenAPI generation failed (%s); falling back to HTTP", e
        )
        return None

    if not isinstance(document, dict):
        logger.warning(
            "In-process OpenAPI generation returned %s, not a dict; "
            "falling back to HTTP", type(document).__name__
        )
        return None

    return document


def resolve_transport_url(schema_url: str) -> str:
    """Rewrite a schema URL pointing at our OWN public host to the internal one.

    The container cannot resolve (or reach) its own public FQDN/published port:
    the public URL tracks the host-published port, which is auto-bumped when
    8000 is busy and in any case is not routable from inside. Ingesting our own
    OpenAPI schema by its public URL therefore died with SCHEMA_FETCH_FAILED.

    Strictly additive: only the scheme+netloc of a URL whose host is one of
    OUR public hosts is swapped for NEXTSEEK_INTERNAL_BASE_URL. Any unrelated
    external URL, and every URL when the internal base is unset, is returned
    untouched. Path, query and fragment are always preserved.

    Same primitive as chat_nextseek.config._resolve_nextseek_base_url.
    """
    internal = (os.getenv("NEXTSEEK_INTERNAL_BASE_URL") or "").strip().rstrip("/")
    if not internal:
        return schema_url

    parts = urlsplit(schema_url)
    if not parts.hostname:
        return schema_url
    if parts.hostname.lower() not in _own_public_hosts():
        return schema_url

    internal_parts = urlsplit(internal)
    if not internal_parts.netloc:
        return schema_url

    rewritten = urlunsplit((
        internal_parts.scheme or parts.scheme,
        internal_parts.netloc,
        internal_parts.path.rstrip("/") + parts.path,
        parts.query,
        parts.fragment,
    ))
    logger.info("Rewrote schema URL %s -> %s (own public host)", schema_url, rewritten)
    return rewritten


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
        # Step 0: our OWN schema route is generated in-process, never fetched
        # over HTTP. #77 put IsAuthenticated on /nextseek_api/schema/, so the
        # anonymous self-fetch this used to do now 401s (#94), and no service
        # credential exists to attach. Strictly additive: if generation is
        # unavailable for any reason we fall through to the HTTP path below.
        if is_self_schema_url(schema_url):
            own_document = _generate_own_schema()
            if own_document is not None:
                logger.info(
                    "Serving our own OpenAPI schema in-process for %s", schema_url
                )
                return self._resolve_refs(own_document, schema_url)
            logger.warning(
                "In-process generation unavailable for our own schema %s; "
                "falling back to the HTTP fetch", schema_url
            )

        # Step 1: HTTP GET. Fetch over the container-internal URL when the
        # caller named our own public host, which is not resolvable from in
        # here; the caller-supplied schema_url is still what gets recorded.
        transport_url = resolve_transport_url(schema_url)
        try:
            response = requests.get(transport_url, timeout=self.timeout)
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
        return self._resolve_refs(parsed, schema_url)

    def _resolve_refs(self, parsed: Any, schema_url: str) -> Any:
        """
        Resolve all $ref pointers in a parsed OpenAPI document.

        Shared by the in-process and HTTP paths of fetch_schema so both report
        resolution failures identically.

        Args:
            parsed: Parsed OpenAPI document (dict from JSON/YAML or generator).
            schema_url: URL the document came from, for error reporting only.

        Returns:
            The document with all $ref resolved, as plain Python objects.

        Raises:
            SchemaFetchError: If $ref resolution fails.
        """
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

        # Handle anyOf, oneOf - simplify all variants, wrap with $ prefix
        for combiner in ("anyOf", "oneOf"):
            if combiner in schema and isinstance(schema[combiner], list) and schema[combiner]:
                variants = [self.simplify_schema(s) for s in schema[combiner]]
                if len(variants) == 1:
                    return variants[0]
                return {f"${combiner}": variants}

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

