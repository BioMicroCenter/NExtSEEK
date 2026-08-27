"""SchemaGenerator guard: published OpenAPI ops carry request/response examples."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate_viewset_conventions.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_viewset_conventions", VALIDATOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


vc = _load_validator()

SKIP_PATH_SUFFIXES = ("/schema/", "/swagger/", "/redoc/")


def _iter_operations(schema: dict):
    for path, methods in schema.get("paths", {}).items():
        if any(path.endswith(suffix) for suffix in SKIP_PATH_SUFFIXES):
            continue
        for method, op in methods.items():
            if method.startswith("x-"):
                continue
            yield path, method.upper(), op


def _has_request_example(op: dict) -> bool:
    body = op.get("requestBody") or {}
    for content in (body.get("content") or {}).values():
        examples = content.get("examples") or {}
        if examples:
            return True
    return False


def _has_response_example(op: dict) -> bool:
    for status, resp in (op.get("responses") or {}).items():
        if not str(status).startswith("2"):
            continue
        for content in (resp.get("content") or {}).values():
            examples = content.get("examples") or {}
            if examples:
                return True
    return False


@pytest.mark.django_db
def test_schema_operations_include_examples():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    missing = []
    for path, method, op in _iter_operations(schema):
        op_id = op.get("operationId") or f"{method} {path}"
        if op_id in vc.SCHEMA_EXAMPLES_OPERATION_ID_ALLOWLIST:
            continue
        if _has_request_example(op) or _has_response_example(op):
            continue
        missing.append(f"{op_id} ({method} {path})")

    assert not missing, "Operations missing OpenApi examples:\n" + "\n".join(missing)
