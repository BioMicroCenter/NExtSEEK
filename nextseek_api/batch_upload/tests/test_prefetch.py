"""Tests for prefetch.py — cached DB validation."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from nextseek_api.batch_upload import prefetch as prefetch_module
from nextseek_api.batch_upload.prefetch import (
    _trim_cache,
    cache_stats,
    clear_caches,
    prefetch_assay_ids,
    prefetch_project_sample_type_links,
    prefetch_sample_type_attributes,
    prefetch_sample_types,
    resolve_sample_type_id,
    validate_assay_ids,
)


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Clear module-level caches before and after each test."""
    clear_caches()
    yield
    clear_caches()


def _make_conn(rows=None):
    """Create a mock connection that returns the given rows."""
    conn = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = rows or []
    result.fetchone.return_value = rows[0] if rows else None
    conn.execute.return_value = result
    return conn


# ── _trim_cache ──────────────────────────────────────────────────────────


class TestTrimCache:
    def test_trims_oldest(self):
        d = {"a": 1, "b": 2, "c": 3, "d": 4}
        _trim_cache(d, 2)
        assert len(d) == 2
        # Only "c" and "d" should remain (oldest removed)
        assert "a" not in d
        assert "b" not in d

    def test_no_trim_when_under_limit(self):
        d = {"a": 1, "b": 2}
        _trim_cache(d, 5)
        assert len(d) == 2

    def test_empty_dict(self):
        d = {}
        _trim_cache(d, 5)
        assert len(d) == 0


# ── prefetch_sample_types ────────────────────────────────────────────────


class TestPrefetchSampleTypes:
    def test_fetches_uncached(self):
        conn = _make_conn(rows=[("NHP_blood", 1), ("D.IMG_scan", 2)])
        result = prefetch_sample_types(["NHP_blood", "D.IMG_scan"], conn)
        assert result == {"NHP_blood": 1, "D.IMG_scan": 2}
        conn.execute.assert_called_once()

    def test_uses_cache_on_second_call(self):
        conn = _make_conn(rows=[("NHP_blood", 1)])
        prefetch_sample_types(["NHP_blood"], conn)
        conn.execute.reset_mock()

        # Second call should not query again
        result = prefetch_sample_types(["NHP_blood"], conn)
        assert result == {"NHP_blood": 1}
        conn.execute.assert_not_called()

    def test_partial_cache_hit(self):
        conn1 = _make_conn(rows=[("NHP_blood", 1)])
        prefetch_sample_types(["NHP_blood"], conn1)

        conn2 = _make_conn(rows=[("D.IMG_scan", 2)])
        result = prefetch_sample_types(["NHP_blood", "D.IMG_scan"], conn2)
        assert result == {"NHP_blood": 1, "D.IMG_scan": 2}
        conn2.execute.assert_called_once()

    def test_missing_title_not_in_result(self):
        conn = _make_conn(rows=[])  # Nothing found
        result = prefetch_sample_types(["NONEXISTENT"], conn)
        assert result == {}


# ── prefetch_assay_ids ───────────────────────────────────────────────────


class TestPrefetchAssayIds:
    def test_fetches_existing_ids(self):
        conn = _make_conn(rows=[(1,), (3,)])
        result = prefetch_assay_ids([1, 2, 3], conn)
        assert result == {1, 3}

    def test_caches_results(self):
        conn = _make_conn(rows=[(1,)])
        prefetch_assay_ids([1], conn)
        conn.execute.reset_mock()

        result = prefetch_assay_ids([1], conn)
        assert result == {1}
        conn.execute.assert_not_called()

    def test_chunking_large_list(self):
        # Create a list larger than 1000 to trigger chunking
        ids = list(range(1, 1100))
        conn = _make_conn(rows=[(i,) for i in range(1, 100)])
        # Need to handle multiple calls
        result_mock = MagicMock()
        result_mock.fetchall.side_effect = [
            [(i,) for i in range(1, 100)],  # first chunk
            [],  # second chunk
        ]
        conn.execute.return_value = result_mock
        result = prefetch_assay_ids(ids, conn)
        # Should have called execute twice (two chunks)
        assert conn.execute.call_count == 2


# ── prefetch_project_sample_type_links ───────────────────────────────────


class TestPrefetchProjectSampleTypeLinks:
    def test_noop_empty_project_id(self):
        conn = MagicMock()
        prefetch_project_sample_type_links(0, [1, 2], conn)
        conn.execute.assert_not_called()

    def test_noop_empty_sample_type_ids(self):
        conn = MagicMock()
        prefetch_project_sample_type_links(1, [], conn)
        conn.execute.assert_not_called()

    def test_creates_missing_links(self):
        # First execute returns existing=[1], second creates [2]
        conn = MagicMock()
        check_result = MagicMock()
        check_result.fetchall.return_value = [(1,)]  # st_id=1 already linked
        insert_result = MagicMock()
        conn.execute.side_effect = [check_result, insert_result]

        prefetch_project_sample_type_links(10, [1, 2], conn)
        assert conn.execute.call_count == 2  # SELECT + INSERT

    def test_no_insert_when_all_exist(self):
        conn = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = [(1,), (2,)]
        conn.execute.return_value = result

        prefetch_project_sample_type_links(10, [1, 2], conn)
        assert conn.execute.call_count == 1  # Only SELECT, no INSERT

    def test_cached_after_first_call(self):
        conn = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = [(1,), (2,)]
        conn.execute.return_value = result

        prefetch_project_sample_type_links(10, [1, 2], conn)
        conn.execute.reset_mock()

        # Second call should hit cache
        prefetch_project_sample_type_links(10, [1, 2], conn)
        conn.execute.assert_not_called()


# ── resolve_sample_type_id ───────────────────────────────────────────────


class TestResolveSampleTypeId:
    def test_resolves_from_db(self):
        conn = MagicMock()
        result = MagicMock()
        result.fetchone.return_value = (42,)
        conn.execute.return_value = result

        st_id = resolve_sample_type_id("NHP_blood", 10, conn)
        assert st_id == 42

    def test_caches_after_resolve(self):
        conn = MagicMock()
        result = MagicMock()
        result.fetchone.return_value = (42,)
        conn.execute.return_value = result

        resolve_sample_type_id("NHP_blood", 10, conn)
        conn.execute.reset_mock()

        st_id = resolve_sample_type_id("NHP_blood", 10, conn)
        assert st_id == 42
        conn.execute.assert_not_called()

    def test_raises_on_not_found(self):
        conn = MagicMock()
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        with pytest.raises(ValueError, match="Sample type not found"):
            resolve_sample_type_id("NONEXISTENT", 10, conn)


# ── validate_assay_ids ───────────────────────────────────────────────────


class TestValidateAssayIds:
    def test_empty_list(self):
        conn = MagicMock()
        valid, missing = validate_assay_ids([], conn)
        assert valid == set()
        assert missing == set()
        conn.execute.assert_not_called()

    def test_all_valid(self):
        conn = _make_conn(rows=[(1,), (2,)])
        valid, missing = validate_assay_ids([1, 2], conn)
        assert valid == {1, 2}
        assert missing == set()

    def test_some_missing(self):
        conn = _make_conn(rows=[(1,)])
        valid, missing = validate_assay_ids([1, 99], conn)
        assert valid == {1}
        assert missing == {99}

    def test_uses_cache_for_known_ids(self):
        conn1 = _make_conn(rows=[(1,)])
        validate_assay_ids([1, 2], conn1)

        conn2 = MagicMock()
        valid, missing = validate_assay_ids([1, 2], conn2)
        assert valid == {1}
        assert missing == {2}
        conn2.execute.assert_not_called()  # All cached


# ── cache_stats / clear_caches ───────────────────────────────────────────


class TestCacheManagement:
    def test_cache_stats_empty(self):
        stats = cache_stats()
        assert stats["assay_exists"] == 0
        assert stats["sample_type_title_to_id"] == 0
        assert stats["project_sample_type_linked"] == 0

    def test_cache_stats_after_population(self):
        conn = _make_conn(rows=[("NHP_blood", 1)])
        prefetch_sample_types(["NHP_blood"], conn)
        stats = cache_stats()
        assert stats["sample_type_title_to_id"] == 1

    def test_clear_caches(self):
        conn = _make_conn(rows=[("NHP_blood", 1)])
        prefetch_sample_types(["NHP_blood"], conn)
        clear_caches()
        stats = cache_stats()
        assert stats["sample_type_title_to_id"] == 0
        assert stats["assay_exists"] == 0


# ── prefetch_sample_type_attributes ──────────────────────────────────────


class TestPrefetchSampleTypeAttributes:
    def test_empty_id_list_returns_empty_dict(self):
        conn = MagicMock()
        result = prefetch_sample_type_attributes([], conn)
        assert result == {}
        conn.execute.assert_not_called()

    def test_single_sample_type_returns_attribute_set(self):
        conn = _make_conn(rows=[
            (1, "UID"),
            (1, "Parent"),
            (1, "Subject ID"),
        ])
        result = prefetch_sample_type_attributes([1], conn)
        assert result == {1: {"UID", "Parent", "Subject ID"}}
        conn.execute.assert_called_once()

    def test_multiple_sample_types_returns_one_entry_per_id(self):
        conn = _make_conn(rows=[
            (1, "UID"),
            (1, "Parent"),
            (2, "UID"),
            (2, "Scan Path"),
            (3, "UID"),
        ])
        result = prefetch_sample_type_attributes([1, 2, 3], conn)
        assert result == {
            1: {"UID", "Parent"},
            2: {"UID", "Scan Path"},
            3: {"UID"},
        }
        conn.execute.assert_called_once()

    def test_cache_hit_issues_no_sql_on_second_call(self):
        conn = _make_conn(rows=[(1, "UID"), (1, "Parent")])
        prefetch_sample_type_attributes([1], conn)
        conn.execute.reset_mock()

        result = prefetch_sample_type_attributes([1], conn)
        assert result == {1: {"UID", "Parent"}}
        conn.execute.assert_not_called()

    def test_unknown_sample_type_id_omitted_not_raised(self):
        # SampleType 99 has no rows in sample_attributes; DB returns nothing for it.
        conn = _make_conn(rows=[(1, "UID")])
        result = prefetch_sample_type_attributes([1, 99], conn)
        assert result == {1: {"UID"}}
        assert 99 not in result


# ── refresh_sample_type_attributes_cache ─────────────────────────────────


class TestRefreshSampleTypeAttributesCache:
    """The batch-level invalidation that lets a schema change be seen without a restart.

    _SAMPLE_TYPE_ATTRIBUTES_CACHE is a module-level dict, so it is per gunicorn
    worker (4 of them, gunicorn.conf.py:2) and no writer in another process can
    reach it. The refresh is therefore a stamp every worker reads from the shared
    database, called once per batch by _run_pre_insert_stages -- not per row, which
    is where prefetch_sample_type_attributes itself is called from (transform.py:79).
    """

    def test_refresh_drops_cached_attributes_when_sample_attributes_changed(self):
        # Establish the current generation, then prime the cache underneath it.
        prefetch_module.refresh_sample_type_attributes_cache(
            _make_conn(rows=[(1, "2026-08-31 11:00:00")])
        )
        conn = _make_conn(rows=[(1, "UID")])
        assert prefetch_sample_type_attributes([1], conn) == {1: {"UID"}}

        # A write lands: 'Notes' is added, moving both the count and updated_at.
        prefetch_module.refresh_sample_type_attributes_cache(
            _make_conn(rows=[(2, "2026-08-31 12:00:00")])
        )

        # The next lookup must return to the database, not serve the pre-change set.
        conn2 = _make_conn(rows=[(1, "UID"), (1, "Notes")])
        assert prefetch_sample_type_attributes([1], conn2) == {1: {"UID", "Notes"}}
        conn2.execute.assert_called_once()

    def test_refresh_keeps_cache_when_sample_attributes_unchanged(self):
        # Pins the reason this is a stamp and not an unconditional clear: with no
        # write in between, the per-row lookups must stay free of SQL.
        stamp = [(3, "2026-08-31 11:00:00")]
        prefetch_module.refresh_sample_type_attributes_cache(_make_conn(rows=stamp))
        prefetch_sample_type_attributes([1], _make_conn(rows=[(1, "UID")]))

        prefetch_module.refresh_sample_type_attributes_cache(_make_conn(rows=stamp))

        conn2 = _make_conn(rows=[(1, "UID")])
        assert prefetch_sample_type_attributes([1], conn2) == {1: {"UID"}}
        conn2.execute.assert_not_called()
