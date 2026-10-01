"""Tests for orphan_resolution module.

The graph half is gone (the sync design, section 8): ``resolve_orphans`` rewrites MySQL and reports the children it
resolved, and the caller enqueues them. The outbox rows are pinned by
``nextseek_api/tests/test_graph_sync_hook_orphans.py``.
"""
import inspect
import json

from unittest.mock import MagicMock, patch

from nextseek_api.batch_upload import orphan_resolution
from nextseek_api.batch_upload.orphan_resolution import (
    discover_orphans,
    resolve_orphans,
)
from nextseek_api.batch_upload.identity import hash_identity


class TestDiscoverOrphans:
    def test_finds_orphan_matching_new_identity(self):
        """Should return orphan sample IDs whose parent_titles match new identities."""
        mock_driver = MagicMock()
        mock_record = MagicMock()
        mock_record.data.return_value = {
            "id": 500,
            "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A", "OtherParent"],
        }
        mock_result = MagicMock()
        mock_result.records = [mock_record]
        mock_driver.execute_query.return_value = mock_result

        identity_map = {"Mouse-A": "MUS-260305MIT-1"}
        orphans = discover_orphans(mock_driver, "nextseekdev", identity_map)

        assert len(orphans) == 1
        assert orphans[0]["id"] == 500
        assert orphans[0]["uuid"] == "CHD-260101MIT-1"
        assert orphans[0]["matched_tokens"] == {"Mouse-A": "MUS-260305MIT-1"}

    def test_no_orphans_when_no_matches(self):
        """Should return empty list when no orphans match."""
        mock_driver = MagicMock()
        mock_result = MagicMock()
        mock_result.records = []
        mock_driver.execute_query.return_value = mock_result

        orphans = discover_orphans(mock_driver, "nextseekdev", {"NoMatch": "NHP-260305MIT-1"})
        assert orphans == []

    def test_empty_identity_map_skips_query(self):
        """Should return empty list without querying Neo4j when identity_map is empty."""
        mock_driver = MagicMock()
        orphans = discover_orphans(mock_driver, "nextseekdev", {})
        assert orphans == []
        mock_driver.execute_query.assert_not_called()

    def test_multiple_orphans_with_different_matches(self):
        """Should handle multiple orphans matching different identities."""
        mock_driver = MagicMock()
        mock_record1 = MagicMock()
        mock_record1.data.return_value = {
            "id": 500,
            "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A"],
        }
        mock_record2 = MagicMock()
        mock_record2.data.return_value = {
            "id": 600,
            "uuid": "CHD-260101MIT-2",
            "parent_titles": ["Mouse-B", "Mouse-A"],
        }
        mock_result = MagicMock()
        mock_result.records = [mock_record1, mock_record2]
        mock_driver.execute_query.return_value = mock_result

        identity_map = {"Mouse-A": "MUS-260305MIT-1", "Mouse-B": "MUS-260305MIT-2"}
        orphans = discover_orphans(mock_driver, "nextseekdev", identity_map)

        assert len(orphans) == 2
        assert orphans[0]["matched_tokens"] == {"Mouse-A": "MUS-260305MIT-1"}
        assert orphans[1]["matched_tokens"] == {
            "Mouse-B": "MUS-260305MIT-2",
            "Mouse-A": "MUS-260305MIT-1",
        }

    def test_passes_correct_parameters_to_neo4j(self):
        """Should pass hashed identity keys as $new_identity_hashes parameter."""
        mock_driver = MagicMock()
        mock_result = MagicMock()
        mock_result.records = []
        mock_driver.execute_query.return_value = mock_result

        identity_map = {"Mouse-A": "MUS-260305MIT-1", "Sample-X": "NHP-260305MIT-2"}
        discover_orphans(mock_driver, "nextseekdev", identity_map)

        call_args = mock_driver.execute_query.call_args
        params = call_args[0][1]
        expected_hashes = {hash_identity("Mouse-A"), hash_identity("Sample-X")}
        assert set(params["new_identity_hashes"]) == expected_hashes
        assert "new_identities" not in params
        assert call_args[1]["database_"] == "nextseekdev"

    def test_cypher_filters_by_parent_title_hashes(self):
        """Cypher WHERE clause should match against child.parent_title_hashes."""
        mock_driver = MagicMock()
        mock_result = MagicMock()
        mock_result.records = []
        mock_driver.execute_query.return_value = mock_result

        discover_orphans(mock_driver, "nextseekdev", {"Mouse-A": "MUS-260305MIT-1"})

        cypher = mock_driver.execute_query.call_args[0][0]
        assert "child.parent_title_hashes" in cypher
        assert "$new_identity_hashes" in cypher
        # Ensure the legacy filter is gone
        assert "name IN child.parent_titles" not in cypher

    def test_post_loop_still_matches_via_raw_parent_titles(self):
        """matched_tokens dict is built from raw parent_titles using exact-case match."""
        mock_driver = MagicMock()
        mock_record = MagicMock()
        mock_record.data.return_value = {
            "id": 500,
            "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A", "Other"],
        }
        mock_result = MagicMock()
        mock_result.records = [mock_record]
        mock_driver.execute_query.return_value = mock_result

        # Identity map has different case AND a non-matching key.
        # The hash prefilter uses lowercased hashes (so "mouse-a" and "Mouse-A"
        # would prefilter the same), but the post-loop is exact-case.
        identity_map = {"mouse-a": "MUS-260305MIT-1"}
        orphans = discover_orphans(mock_driver, "nextseekdev", identity_map)
        # The Neo4j prefilter is mocked, so we received the candidate row;
        # the post-loop must NOT match because "Mouse-A" != "mouse-a" exactly.
        assert orphans == []

    def test_orphan_with_none_parent_titles(self):
        """Should handle records where parent_titles is None."""
        mock_driver = MagicMock()
        mock_record = MagicMock()
        mock_record.data.return_value = {
            "id": 700,
            "uuid": "CHD-260101MIT-3",
            "parent_titles": None,
        }
        mock_result = MagicMock()
        mock_result.records = [mock_record]
        mock_driver.execute_query.return_value = mock_result

        identity_map = {"Mouse-A": "MUS-260305MIT-1"}
        orphans = discover_orphans(mock_driver, "nextseekdev", identity_map)

        # Record with None parent_titles should not produce a match
        assert orphans == []


def _rewrite_conn(metadata: str):
    """A sql_conn whose first execute() answers the metadata fetch and whose second takes the UPDATE."""
    conn = MagicMock()
    fetch = MagicMock()
    fetch.fetchone.return_value = (metadata,)
    conn.execute.side_effect = [fetch, MagicMock()]
    return conn


def _orphan(sample_id=500, uuid="CHD-260101MIT-1", titles=("Mouse-A",)):
    return {
        "id": sample_id,
        "uuid": uuid,
        "parent_titles": list(titles),
        "matched_tokens": {"Mouse-A": "MUS-260305MIT-1"},
    }


class TestResolveOrphans:
    def test_replaces_identity_with_uid_in_parent_field(self):
        """Should replace identity token with UID in json_metadata.Parent."""
        conn = _rewrite_conn('{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"Mouse-A"}')

        stats = resolve_orphans(orphans=[_orphan()], sql_conn=conn)

        assert stats == {"resolved": 1, "sample_ids": [500]}
        written = json.loads(conn.execute.call_args_list[1][0][1]["meta"])
        assert written["Parent"] == "MUS-260305MIT-1"

    def test_skips_already_resolved_parent(self):
        """If the identity token is NOT in the Parent field (already resolved), skip."""
        conn = MagicMock()
        fetch = MagicMock()
        fetch.fetchone.return_value = (
            '{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"MUS-260305MIT-1"}',
        )
        conn.execute.return_value = fetch

        stats = resolve_orphans(orphans=[_orphan()], sql_conn=conn)

        assert stats == {"resolved": 0, "sample_ids": []}

    def test_a_row_with_no_metadata_is_skipped(self):
        conn = MagicMock()
        fetch = MagicMock()
        fetch.fetchone.return_value = None
        conn.execute.return_value = fetch

        assert resolve_orphans(orphans=[_orphan()], sql_conn=conn) == {"resolved": 0, "sample_ids": []}

    def test_every_resolved_child_is_reported_once(self):
        """One id per child, in the order they were resolved: the caller enqueues them."""
        conn = MagicMock()
        first, second = MagicMock(), MagicMock()
        first.fetchone.return_value = ('{"UID":"CHD-1","Parent":"Mouse-A"}',)
        second.fetchone.return_value = ('{"UID":"CHD-2","Parent":"Mouse-A;Mouse-A"}',)
        conn.execute.side_effect = [first, MagicMock(), second, MagicMock()]

        stats = resolve_orphans(
            orphans=[_orphan(500), _orphan(600, uuid="CHD-260101MIT-2")], sql_conn=conn,
        )

        assert stats == {"resolved": 2, "sample_ids": [500, 600]}

    def test_does_not_modify_parent_titles(self):
        """parent_titles is permanent metadata: the rewrite touches the Parent field alone."""
        conn = _rewrite_conn('{"UID":"CHD-260101MIT-1","Parent":"Mouse-A"}')

        resolve_orphans(orphans=[_orphan()], sql_conn=conn)

        written = json.loads(conn.execute.call_args_list[1][0][1]["meta"])
        assert "parent_titles" not in written and "parent_title_hashes" not in written

    def test_partial_resolution_keeps_remaining_tokens(self):
        """If 2 unresolved parents and only 1 matches, keep the other in Parent field."""
        conn = _rewrite_conn(
            '{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"Mouse-A;StillUnresolved"}'
        )

        stats = resolve_orphans(
            orphans=[_orphan(titles=("Mouse-A", "StillUnresolved"))], sql_conn=conn,
        )

        assert stats["resolved"] == 1
        updated_meta = conn.execute.call_args_list[1][0][1]["meta"]
        assert "MUS-260305MIT-1" in updated_meta, "Mouse-A should be replaced with UID"
        assert "StillUnresolved" in updated_meta, "StillUnresolved should be kept"
        assert "Mouse-A" not in updated_meta, "Mouse-A identity token should be gone"


class TestResolveOrphansWritesNoGraph:
    """The one code path: every graph write is graph_sync's (the sync design, sections 7 and 8)."""

    def test_it_takes_no_neo4j_driver(self):
        assert list(inspect.signature(resolve_orphans).parameters) == ["orphans", "sql_conn"]

    def test_the_module_holds_no_write_cypher(self):
        assert not hasattr(orphan_resolution, "_DERIVED_FROM_CYPHER")
        assert [n for n in dir(orphan_resolution) if n.endswith("_CYPHER")] == ["_DISCOVER_CYPHER"]
        assert "MERGE" not in orphan_resolution._DISCOVER_CYPHER

    def test_the_only_write_left_is_the_mysql_rewrite(self):
        source = inspect.getsource(orphan_resolution)
        assert source.count("SET ") == 1 and "UPDATE samples SET json_metadata" in source

    def test_nothing_is_imported_from_neo4j(self):
        source = inspect.getsource(orphan_resolution)
        assert "import neo4j" not in source and "from neo4j" not in source


class TestResolveOrphansVariantKeys:
    """Test that resolve_orphans reads ALL parent-containing keys."""

    def test_matched_token_in_variant_key_resolved(self):
        """If the matched token is in a variant key (not Parent), it should still be resolved."""
        conn = _rewrite_conn('{"UID":"CHD-260101MIT-1","Name":"child1","Treatment1Parent":"Mouse-A"}')

        stats = resolve_orphans(orphans=[_orphan()], sql_conn=conn)

        assert stats == {"resolved": 1, "sample_ids": [500]}

    def test_variant_key_not_modified_by_resolution(self):
        """Variant keys should NOT be modified: only the Parent key is updated."""
        conn = _rewrite_conn('{"UID":"CHD-260101MIT-1","Name":"child1","Treatment1Parent":"Mouse-A"}')

        resolve_orphans(orphans=[_orphan()], sql_conn=conn)

        written = json.loads(conn.execute.call_args_list[1][0][1]["meta"])
        assert written["Treatment1Parent"] == "Mouse-A"

    def test_parent_and_variant_both_contribute_tokens(self):
        """Tokens from Parent + variant key should both be available for matching."""
        conn = _rewrite_conn(
            '{"UID":"CHD-260101MIT-1","Parent":"Unresolved_A","Treatment1Parent":"Mouse-A"}'
        )

        stats = resolve_orphans(orphans=[_orphan()], sql_conn=conn)

        assert stats["resolved"] == 1
        written = json.loads(conn.execute.call_args_list[1][0][1]["meta"])
        assert "MUS-260305MIT-1" in written["Parent"]


class TestResolveOrphansTask:
    def test_task_runs_discovery_and_resolution(self):
        from nextseek_api.batch_upload.tasks import resolve_orphans_task

        with patch("nextseek_api.batch_upload.orphan_resolution.discover_orphans") as mock_discover, \
             patch("nextseek_api.batch_upload.orphan_resolution.resolve_orphans") as mock_resolve, \
             patch("nextseek_api.graph_sync.hooks.enqueue") as mock_enqueue, \
             patch("neo4j.GraphDatabase") as mock_gdb, \
             patch("nextseek_api.batch_upload.config.Neo4jConfig.from_django_settings") as mock_config_cls, \
             patch("nextseek_api.batch_upload.db_engine.get_connection") as mock_get_conn:

            mock_config = MagicMock(
                NEO4J_UPLOAD_ENABLED=True, URI="bolt://localhost",
                NEO4J_USER="u", PASSWORD="p", NEO4J_DB="db",
            )
            mock_config_cls.return_value = mock_config

            mock_driver = MagicMock()
            mock_gdb.driver.return_value = mock_driver

            mock_discover.return_value = [{"id": 1, "uuid": "A", "matched_tokens": {"X": "Y"}}]
            mock_resolve.return_value = {"resolved": 1, "sample_ids": [1]}

            mock_conn = MagicMock()
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

            result = resolve_orphans_task(
                identity_map={"X": "Y"},
                parent_info={"Y": {"sample_id": 1, "uuid": "Y"}},
            )

            mock_discover.assert_called_once()
            mock_resolve.assert_called_once_with(orphans=mock_discover.return_value, sql_conn=mock_conn)
            mock_enqueue.assert_called_once_with("samples", "sample:1")
            assert result["resolved"] == 1

    def test_empty_identity_map_returns_zero(self):
        from nextseek_api.batch_upload.tasks import resolve_orphans_task

        result = resolve_orphans_task(identity_map={}, parent_info={})
        assert result == {"resolved": 0}

    def test_neo4j_disabled_returns_zero(self):
        from nextseek_api.batch_upload.tasks import resolve_orphans_task

        with patch("neo4j.GraphDatabase"), \
             patch("nextseek_api.batch_upload.config.Neo4jConfig.from_django_settings") as mock_config_cls:

            mock_config_cls.return_value = MagicMock(NEO4J_UPLOAD_ENABLED=False)

            result = resolve_orphans_task(
                identity_map={"X": "Y"},
                parent_info={"Y": {"sample_id": 1, "uuid": "Y"}},
            )
            assert result == {"resolved": 0}

    def test_exception_returns_error_dict(self):
        from nextseek_api.batch_upload.tasks import resolve_orphans_task

        with patch("neo4j.GraphDatabase") as mock_gdb, \
             patch("nextseek_api.batch_upload.config.Neo4jConfig.from_django_settings") as mock_config_cls:

            mock_config_cls.return_value = MagicMock(
                NEO4J_UPLOAD_ENABLED=True, URI="bolt://localhost",
                NEO4J_USER="u", PASSWORD="p", NEO4J_DB="db",
            )
            mock_gdb.driver.side_effect = RuntimeError("connection failed")

            result = resolve_orphans_task(
                identity_map={"X": "Y"},
                parent_info={"Y": {"sample_id": 1, "uuid": "Y"}},
            )
            assert result["resolved"] == 0
            assert "error" in result


class TestOrphanResolutionIntegration:
    """End-to-end test with a mocked MariaDB and a mocked Neo4j read."""

    def test_full_orphan_resolution_flow(self):
        """Upload orphan, upload parent, verify the rewrite and the reported child."""
        mock_driver = MagicMock()

        orphan_meta = '{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"Mouse-A"}'
        identity_map = {"Mouse-A": "MUS-260305MIT-1"}

        orphan_record = MagicMock()
        orphan_record.data.return_value = {
            "id": 500,
            "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A"],
        }
        discover_result = MagicMock()
        discover_result.records = [orphan_record]
        mock_driver.execute_query.return_value = discover_result

        orphans = discover_orphans(mock_driver, "testdb", identity_map)
        assert len(orphans) == 1
        assert orphans[0]["matched_tokens"] == {"Mouse-A": "MUS-260305MIT-1"}

        conn = _rewrite_conn(orphan_meta)
        stats = resolve_orphans(orphans=orphans, sql_conn=conn)

        assert stats == {"resolved": 1, "sample_ids": [500]}
        # The discovery read is the only statement the graph saw.
        assert mock_driver.execute_query.call_count == 1
