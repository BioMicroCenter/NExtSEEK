"""Strict public scalar parsing for the attribute API."""
from __future__ import annotations

from dataclasses import dataclass

from .schemas import AttributeErrorResponse, MutationError

MAX_PUBLIC_INTEGER = 2**63 - 1


@dataclass(frozen=True)
class ScalarInputError(ValueError):
    field: str
    submitted_value: object
    message: str

    def as_attribute_error_response(self) -> dict:
        return AttributeErrorResponse(errors=[MutationError(
            code="invalid_scalar",
            message=self.message,
            field=self.field,
            submitted_identifier=self.submitted_value,
        )]).model_dump(mode="json")


def parse_positive_int(value, *, field: str, maximum: int = MAX_PUBLIC_INTEGER) -> int:
    """Accept one ASCII-decimal scalar and reject coercive/multi-value input."""
    submitted = value
    if isinstance(value, (list, tuple)):
        raise ScalarInputError(field, submitted, f"{field} must be supplied exactly once")
    if isinstance(value, bool):
        raise ScalarInputError(field, submitted, f"{field} must be a positive integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
    else:
        raise ScalarInputError(field, submitted, f"{field} must be an ASCII positive integer")
    if parsed < 1 or parsed > maximum:
        raise ScalarInputError(field, submitted, f"{field} must be between 1 and {maximum}")
    return parsed


def parse_query_positive_int(query, field: str, *, default: int, maximum: int) -> int:
    if field not in query:
        return default
    values = query.getlist(field) if hasattr(query, "getlist") else query.get(field)
    if not isinstance(values, (list, tuple)):
        values = [values]
    if len(values) != 1:
        raise ScalarInputError(field, values, f"{field} must be supplied exactly once")
    return parse_positive_int(values[0], field=field, maximum=maximum)
