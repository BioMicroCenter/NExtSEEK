"""JSON:API error response helpers for v2 endpoints.

Public API:
    build_error(...)                  - construct one error object.
    api_error(...)                    - return a Response wrapping one error.
    api_errors(...)                   - return a Response wrapping a list.
    JSONAPI_V2_MEDIA_TYPE             - the v2 content-type constant.
"""
import json
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


def translate_error_response_v2(resp, request):
    """Convert a legacy HttpResponse error body to v2 JSON:API shape.

    Idempotent: applying twice is a no-op. Pass-through for 2xx, v1, or
    unversioned requests.
    """
    status_code = getattr(resp, "status_code", 200)
    if not (400 <= status_code < 600):
        return resp
    if getattr(request, "version", None) != "v2":
        return resp

    try:
        content = getattr(resp, "content", b"")
        body = json.loads((content or b"").decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return api_errors(status_code, [build_error(
            status_code,
            "Upstream error",
            detail="Upstream returned non-JSON response",
        )])

    if isinstance(body, dict) and isinstance(body.get("errors"), list):
        return api_errors(status_code, [
            build_error(
                err.get("status", status_code),
                err.get("title", "Error"),
                detail=err.get("detail"),
                pointer=(err.get("source") or {}).get("pointer"),
                parameter=(err.get("source") or {}).get("parameter"),
                valid_values=(err.get("meta") or {}).get("valid_values"),
                example=(err.get("meta") or {}).get("example"),
            )
            for err in body["errors"]
        ])

    detail = body.get("detail") if isinstance(body, dict) else None
    return api_errors(status_code, [build_error(
        status_code,
        detail or "Error",
    )])
