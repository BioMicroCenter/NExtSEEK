"""Pure, database-free unit tests for DD-20 page-number pagination."""
from __future__ import annotations

import pytest

from nextseek_api.attributes.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    PageRequest,
    paginate,
)
from nextseek_api.attributes.schemas import Pagination


class TestPageRequest:
    def test_defaults(self):
        request = PageRequest()
        assert request.page == 1
        assert request.page_size == DEFAULT_PAGE_SIZE
        assert request.offset == 0

    def test_offset_for_page_two(self):
        request = PageRequest(page=2, page_size=100)
        assert request.offset == 100

    def test_offset_for_page_three_default_size(self):
        request = PageRequest(page=3)
        assert request.offset == 2 * DEFAULT_PAGE_SIZE

    def test_page_size_at_maximum_is_accepted(self):
        request = PageRequest(page=1, page_size=MAX_PAGE_SIZE)
        assert request.page_size == MAX_PAGE_SIZE

    def test_page_size_above_maximum_is_rejected(self):
        with pytest.raises(ValueError, match="page_size"):
            PageRequest(page=1, page_size=MAX_PAGE_SIZE + 1)

    def test_page_size_zero_is_rejected(self):
        with pytest.raises(ValueError, match="page_size"):
            PageRequest(page=1, page_size=0)

    def test_page_size_negative_is_rejected(self):
        with pytest.raises(ValueError, match="page_size"):
            PageRequest(page=1, page_size=-1)

    def test_page_zero_is_rejected(self):
        with pytest.raises(ValueError, match="page"):
            PageRequest(page=0, page_size=10)

    def test_page_negative_is_rejected(self):
        with pytest.raises(ValueError, match="page"):
            PageRequest(page=-1, page_size=10)

    def test_page_bool_is_rejected(self):
        with pytest.raises(ValueError, match="page"):
            PageRequest(page=True, page_size=10)

    def test_page_size_bool_is_rejected(self):
        with pytest.raises(ValueError, match="page_size"):
            PageRequest(page=1, page_size=True)

    def test_page_non_int_is_rejected(self):
        with pytest.raises(ValueError, match="page"):
            PageRequest(page=1.5, page_size=10)

    def test_page_request_is_frozen(self):
        request = PageRequest()
        with pytest.raises(AttributeError):
            request.page = 2


class TestPaginate:
    def test_empty_result_set(self):
        page = paginate([], total=0, request=PageRequest())
        assert page.attributes == ()
        assert page.pagination.total_records == 0
        assert page.pagination.total_pages == 0

    def test_single_full_page(self):
        page = paginate(["a", "b", "c"], total=3, request=PageRequest(page=1, page_size=10))
        assert page.attributes == ("a", "b", "c")
        assert page.pagination.total_records == 3
        assert page.pagination.total_pages == 1
        assert page.pagination.page == 1
        assert page.pagination.page_size == 10

    def test_total_pages_rounds_up(self):
        page = paginate(["a"], total=101, request=PageRequest(page=1, page_size=100))
        assert page.pagination.total_pages == 2

    def test_total_pages_exact_division(self):
        page = paginate(["a"], total=100, request=PageRequest(page=1, page_size=100))
        assert page.pagination.total_pages == 1

    def test_attributes_are_materialized_as_a_tuple(self):
        page = paginate(iter(["a", "b"]), total=2, request=PageRequest())
        assert isinstance(page.attributes, tuple)

    def test_pagination_field_is_the_schema_model(self):
        page = paginate([], total=0, request=PageRequest())
        assert isinstance(page.pagination, Pagination)

    def test_page_object_carries_page_and_page_size_through(self):
        page = paginate([], total=0, request=PageRequest(page=4, page_size=250))
        assert page.pagination.page == 4
        assert page.pagination.page_size == 250

    def test_page_is_frozen(self):
        page = paginate([], total=0, request=PageRequest())
        with pytest.raises(AttributeError):
            page.attributes = ("x",)

    def test_page_generic_alias_instantiates(self):
        # Page[T] is a plain dataclass; direct construction should also work.
        pagination = Pagination(page=1, page_size=10, total_records=0, total_pages=0)
        page = Page(attributes=(), pagination=pagination)
        assert page.attributes == ()
