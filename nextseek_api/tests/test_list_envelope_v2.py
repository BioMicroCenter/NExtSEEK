"""Unit tests for build_v2_list_envelope."""
import pytest
from rest_framework.test import APIRequestFactory

from nextseek_api.helpers import build_v2_list_envelope


@pytest.fixture
def factory():
    return APIRequestFactory()


class TestBuildV2ListEnvelope:
    def test_flat_list_empty(self, factory):
        req = factory.get("/nextseek_api/samples/advanced_search/")
        env = build_v2_list_envelope(req, [])
        assert env == {"results": [], "count": 0, "next": None, "previous": None}

    def test_flat_list_single_page(self, factory):
        req = factory.get("/nextseek_api/samples/advanced_search/")
        rows = [{"id": i} for i in range(5)]
        env = build_v2_list_envelope(req, rows)
        assert env["results"] == rows
        assert env["count"] == 5
        assert env["next"] is None
        assert env["previous"] is None

    def test_pagination_middle_page(self, factory):
        req = factory.get("/nextseek_api/samples/advanced_search/?page=2&page_size=10")
        rows = [{"id": i} for i in range(25)]
        env = build_v2_list_envelope(req, rows)
        assert len(env["results"]) == 10
        assert env["count"] == 25
        assert env["next"] is not None and "page=3" in env["next"]
        assert env["previous"] is not None

    def test_count_override_wins_over_page_length(self, factory):
        req = factory.get("/nextseek_api/samples/advanced_search/")
        rows = [{"id": 1}]
        env = build_v2_list_envelope(req, rows, count=999)
        assert env["count"] == 999

    def test_count_override_none_falls_back_to_paginator_count(self, factory):
        req = factory.get("/nextseek_api/samples/advanced_search/")
        rows = [{"id": i} for i in range(3)]
        env = build_v2_list_envelope(req, rows, count=None)
        assert env["count"] == 3

    def test_does_not_mutate_input_rows(self, factory):
        req = factory.get("/nextseek_api/samples/advanced_search/")
        rows = [{"id": 1}, {"id": 2}]
        original = list(rows)
        build_v2_list_envelope(req, rows)
        assert rows == original

    def test_no_v1_keys_in_result(self, factory):
        req = factory.get("/nextseek_api/samples/advanced_search/")
        env = build_v2_list_envelope(req, [{"id": 1}])
        for v1_key in ("rows", "total", "sampleTypes", "noSampleTypes", "footer"):
            assert v1_key not in env

    def test_result_keys_exactly(self, factory):
        req = factory.get("/nextseek_api/samples/advanced_search/")
        env = build_v2_list_envelope(req, [])
        assert set(env.keys()) == {"results", "count", "next", "previous"}

    def test_page_size_zero_falls_back_to_default_page_size(self, factory):
        """TDD-12 (amended AMD-12): ?page_size=0 raises ValueError in DRF's _positive_int
        (strict=True), which PageNumberPagination.get_page_size suppresses and falls back
        to self.page_size (default 100). So paginate_queryset returns the original page,
        not None. Documents the actual contract."""
        req = factory.get("/nextseek_api/samples/advanced_search/?page_size=0")
        env = build_v2_list_envelope(req, [{"id": 1}])
        assert env["results"] == [{"id": 1}]
        assert env["count"] == 1
