"""Version-aware DRF exception handler.

v1 / no version: delegates to DRF's default `exception_handler`.
v2: reshapes DRF validation/auth/permission/not-found exceptions to JSON:API errors[].
"""
from typing import Any, Optional, Sequence

from rest_framework import exceptions as drf_exc
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler

from .errors import api_errors, build_error


def handle_api_exception(exc, context) -> Optional[Response]:
    request = context.get("request")
    version = getattr(request, "version", None)
    if version != "v2":
        return drf_default_handler(exc, context)
    reshaped = _reshape_for_v2(exc)
    if reshaped is None:
        return drf_default_handler(exc, context)
    return reshaped


def _reshape_for_v2(exc) -> Optional[Response]:
    if isinstance(exc, drf_exc.ValidationError):
        return _reshape_validation(exc)
    if isinstance(exc, drf_exc.NotFound):
        return api_errors(404, [build_error(404, "Not found", detail=str(exc.detail))])
    if isinstance(exc, drf_exc.PermissionDenied):
        return api_errors(403, [build_error(403, "Permission denied", detail=str(exc.detail))])
    if isinstance(exc, drf_exc.NotAuthenticated):
        return api_errors(401, [build_error(401, "Authentication required", detail=str(exc.detail))])
    if isinstance(exc, drf_exc.AuthenticationFailed):
        return api_errors(401, [build_error(401, "Authentication failed", detail=str(exc.detail))])
    return None


def _reshape_validation(exc: drf_exc.ValidationError) -> Response:
    errors: list[dict] = []
    for loc, messages in _iter_field_errors(exc.detail):
        pointer = pointer_from_loc(loc) if loc else None
        for msg in messages:
            errors.append(build_error(
                400, "Validation error", detail=str(msg), pointer=pointer,
            ))
    if not errors:
        errors = [build_error(400, "Validation error", detail=str(exc.detail))]
    return api_errors(400, errors)


def _iter_field_errors(detail, prefix: Sequence[Any] = ()):
    """Yield (loc_tuple, [messages]) pairs from a DRF ValidationError detail.

    DRF details come in three shapes:
      - dict[field, list|dict]      - per-field errors
      - list[message|dict]          - list of messages or per-item errors
      - scalar                      - flat message
    """
    if isinstance(detail, dict):
        for key, val in detail.items():
            yield from _iter_field_errors(val, (*prefix, key))
        return
    if isinstance(detail, list):
        messages: list[Any] = []
        for idx, item in enumerate(detail):
            if isinstance(item, (dict, list)):
                yield from _iter_field_errors(item, (*prefix, idx))
            else:
                messages.append(item)
        if messages:
            yield prefix, messages
        return
    yield prefix, [detail]


def pointer_from_loc(loc: Sequence[Any]) -> str:
    """Map a DRF/Pydantic location tuple to a JSON:API pointer.

    ('title',)              -> '/data/attributes/title'
    ('rows', 0, 'title')    -> '/data/attributes/rows/0/title'
    ()                      -> '/data/attributes/'
    """
    parts = [str(p) for p in loc]
    return "/data/attributes/" + "/".join(parts)
