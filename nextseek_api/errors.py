"""JSON:API error response helpers for v2 endpoints.

Public API:
    build_error(...)                  - construct one error object.
    api_error(...)                    - return a Response wrapping one error.
    api_errors(...)                   - return a Response wrapping a list.
    JSONAPI_V2_MEDIA_TYPE             - the v2 content-type constant.
"""
from typing import Any, Iterable, Optional

from rest_framework.response import Response

JSONAPI_V2_MEDIA_TYPE = "application/vnd.nextseek.v2+json"


def build_error(
    status: int,
    title: str,
    *,
    detail: Optional[str] = None,
    pointer: Optional[str] = None,
    parameter: Optional[str] = None,
    valid_values: Optional[Iterable[Any]] = None,
    example: Optional[Any] = None,
) -> dict:
    err: dict[str, Any] = {"status": str(status), "title": title}
    if detail:
        err["detail"] = detail
    source: dict[str, Any] = {}
    if pointer:
        source["pointer"] = pointer
    if parameter:
        source["parameter"] = parameter
    if source:
        err["source"] = source
    meta: dict[str, Any] = {}
    if valid_values is not None:
        meta["valid_values"] = list(valid_values)
    if example is not None:
        meta["example"] = example
    if meta:
        err["meta"] = meta
    return err


# Private-underscore alias for symbols originally sketched under _build_error.
# Retained so task-03 imports that reference _build_error still resolve.
_build_error = build_error


def api_error(
    status: int,
    title: str,
    *,
    detail: Optional[str] = None,
    pointer: Optional[str] = None,
    parameter: Optional[str] = None,
    valid_values: Optional[Iterable[Any]] = None,
    example: Optional[Any] = None,
) -> Response:
    err = build_error(
        status, title,
        detail=detail, pointer=pointer, parameter=parameter,
        valid_values=valid_values, example=example,
    )
    return api_errors(status, [err])


def api_errors(status: int, errors: Iterable[dict]) -> Response:
    body = {"errors": list(errors)}
    return Response(body, status=status, content_type=JSONAPI_V2_MEDIA_TYPE)
