"""Tests for backfill_parent_title_hashes script."""
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.identity import hash_identity


class TestComputeHashUpdates:
    def test_skips_samples_with_no_parent_titles(self):
        from nextseek_api.batch_upload.scripts.backfill_parent_title_hashes import (
            compute_hash_updates,
        )
        rows = [
            {"uuid": "NHP-260225MIT-1", "parent_titles": None},
            {"uuid": "NHP-260225MIT-2", "parent_titles": []},
        ]
        assert compute_hash_updates(rows) == []

    def test_hashes_each_title_via_hash_identity(self):
        from nextseek_api.batch_upload.scripts.backfill_parent_title_hashes import (
            compute_hash_updates,
        )
        rows = [
            {"uuid": "NHP-260225MIT-3", "parent_titles": ["Mouse-A", "Other"]},
        ]
        updates = compute_hash_updates(rows)
        assert len(updates) == 1
        assert updates[0]["uuid"] == "NHP-260225MIT-3"
        assert updates[0]["parent_title_hashes"] == [
            hash_identity("Mouse-A"),
            hash_identity("Other"),
        ]

    def test_filters_none_hashes(self):
        """Empty/whitespace titles produce None from hash_identity and are filtered."""
        from nextseek_api.batch_upload.scripts.backfill_parent_title_hashes import (
            compute_hash_updates,
        )
        rows = [
            {"uuid": "NHP-260225MIT-4", "parent_titles": ["Real", "   ", ""]},
        ]
        updates = compute_hash_updates(rows)
        assert updates[0]["parent_title_hashes"] == [hash_identity("Real")]


class TestBatchWrite:
    def test_writes_in_chunks_of_batch_size(self):
        from nextseek_api.batch_upload.scripts.backfill_parent_title_hashes import (
            BATCH_SIZE,
            write_hash_updates,
        )
        # BATCH_SIZE+5 updates should produce 2 driver calls.
        updates = [
            {"uuid": f"NHP-{i:06d}MIT-1", "parent_title_hashes": ["a" * 64]}
            for i in range(BATCH_SIZE + 5)
        ]
        mock_driver = MagicMock()
        write_hash_updates(mock_driver, "testdb", updates)
        assert mock_driver.execute_query.call_count == 2

    def test_cypher_writes_parent_title_hashes(self):
        from nextseek_api.batch_upload.scripts.backfill_parent_title_hashes import (
            write_hash_updates,
        )
        updates = [{"uuid": "NHP-260225MIT-1", "parent_title_hashes": ["abc"]}]
        mock_driver = MagicMock()
        write_hash_updates(mock_driver, "testdb", updates)
        cypher = mock_driver.execute_query.call_args[0][0]
        assert "MATCH (s:Sample {uuid: row.uuid})" in cypher
        assert "SET s.parent_title_hashes = row.parent_title_hashes" in cypher

    def test_no_calls_for_empty_updates(self):
        from nextseek_api.batch_upload.scripts.backfill_parent_title_hashes import (
            write_hash_updates,
        )
        mock_driver = MagicMock()
        write_hash_updates(mock_driver, "testdb", [])
        mock_driver.execute_query.assert_not_called()


class TestLegacyBackfillWritesHashes:
    """Regression: backfill_parent_titles.py must also write parent_title_hashes."""

    def test_script_imports_hash_identity_and_cypher_sets_both_fields(self):
        """The legacy script's source must import hash_identity and write both fields."""
        import inspect
        from nextseek_api.batch_upload.scripts import backfill_parent_titles
        src = inspect.getsource(backfill_parent_titles)
        assert "from nextseek_api.batch_upload.identity import" in src
        assert "hash_identity" in src
        assert "SET s.parent_titles = row.parent_titles" in src
        assert "s.parent_title_hashes = row.parent_title_hashes" in src
