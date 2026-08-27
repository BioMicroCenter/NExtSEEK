"""DD-20 page-number pagination: stable order, default 500, maximum 5,000.

This module owns only the request/response shape. The stable global order
itself -- ``(sample_type_id, pos, id)`` using DD-35 logical ``pos`` -- and the
bounded count/slice queries live in ``repository.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Generic, TypeVar

from nextseek_api.attributes.schemas import Pagination

DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 5000

T = TypeVar("T")


@dataclass(frozen=True)
class PageRequest:
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if not isinstance(self.page, int) or isinstance(self.page, bool) or self.page < 1:
            raise ValueError("page must be a positive integer")
        if (
            not isinstance(self.page_size, int)
            or isinstance(self.page_size, bool)
            or self.page_size < 1
            or self.page_size > MAX_PAGE_SIZE
        ):
            raise ValueError(f"page_size must be an integer between 1 and {MAX_PAGE_SIZE}")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


@dataclass(frozen=True)
class Page(Generic[T]):
    """A stable page of already-ordered records plus total-record metadata."""

    attributes: tuple[T, ...]
    pagination: Pagination


def paginate(attributes, total: int, request: PageRequest) -> Page:
    total_pages = ceil(total / request.page_size) if total else 0
    pagination = Pagination(
        page=request.page,
        page_size=request.page_size,
        total_records=total,
        total_pages=total_pages,
    )
    return Page(attributes=tuple(attributes), pagination=pagination)
