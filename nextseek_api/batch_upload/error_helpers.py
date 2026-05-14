"""v2-aware error helpers for batch_upload view sites."""
from typing import Any

from rest_framework.response import Response

from nextseek_api.errors import api_error, api_errors, build_error
from nextseek_api.exception_handler import pointer_from_loc


def v1_or_v2_error(request, *, v1_body: dict, v1_status: int, v2: dict) -> Response:
    """Return v2 JSON:API errors[] under v2; DRF Response({"detail":...}) otherwise.

    `v2` is a kwargs dict passed to `api_error(status=v1_status, **v2)`.
    Safety: filters out any `status` key from v2 to prevent duplicate-kwarg TypeError.
    """
    if getattr(request, "version", None) == "v2":
        v2_kwargs = {k: v for k, v in v2.items() if k != "status"}
        return api_error(status=v1_status, **v2_kwargs)
    return Response(v1_body, status=v1_status)


def pydantic_errors_to_api_errors(exc) -> list[dict]:
    """Convert Pydantic v2 ValidationError to JSON:API error objects.

    Uses the human-readable `msg` as `title`; preserves `type` under
    `meta.pydantic_type` for advanced consumers.
    """
    out: list[dict] = []
    for e in exc.errors():
        loc = e.get("loc", ())
        pointer = pointer_from_loc(loc) if loc else None
        out.append(build_error(
            422,
            e.get("msg", "Validation error"),
            pointer=pointer,
        ) | {"meta": {"pydantic_type": e.get("type", "validation_error")}})
    return out
