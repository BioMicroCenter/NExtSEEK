"""Tests for orphan_resolution module."""
import pytest
from unittest.mock import MagicMock, patch
from nextseek_api.batch_upload.orphan_resolution import discover_orphans


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
        """Should pass identity keys as $new_identities parameter."""
        mock_driver = MagicMock()
        mock_result = MagicMock()
        mock_result.records = []
        mock_driver.execute_query.return_value = mock_result

        identity_map = {"Mouse-A": "MUS-260305MIT-1", "Sample-X": "NHP-260305MIT-2"}
        discover_orphans(mock_driver, "nextseekdev", identity_map)

        call_args = mock_driver.execute_query.call_args
        params = call_args[0][1]
        assert set(params["new_identities"]) == {"Mouse-A", "Sample-X"}
        assert call_args[1]["database_"] == "nextseekdev"

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


class TestResolveOrphans:
    def test_replaces_identity_with_uid_in_parent_field(self):
        """Should replace identity token with UID in json_metadata.Parent."""
        from nextseek_api.batch_upload.orphan_resolution import resolve_orphans

        mock_conn = MagicMock()
        mock_driver = MagicMock()

        orphan_meta = '{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"Mouse-A","Protocol":"https://fairdata-dev.mit.edu/sops/57"}'
        mock_fetch = MagicMock()
        mock_fetch.fetchone.return_value = (orphan_meta,)
        sop_result = MagicMock()
        sop_result.fetchone.return_value = ("CD8 Depletion Protocol",)
        mock_conn.execute.side_effect = [mock_fetch, sop_result, MagicMock()]

        mock_driver.execute_query.return_value = MagicMock(records=[])

        orphans = [{
            "id": 500, "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A"],
            "matched_tokens": {"Mouse-A": "MUS-260305MIT-1"},
        }]
        parent_info = {"MUS-260305MIT-1": {"sample_id": 200, "uuid": "MUS-260305MIT-1"}}

        stats = resolve_orphans(
            orphans=orphans, parent_info=parent_info,
            sql_conn=mock_conn, neo4j_driver=mock_driver, neo4j_database="testdb",
        )

        assert stats["resolved"] == 1
        assert stats["edges_created"] >= 1

    def test_skips_already_resolved_parent(self):
        """If the identity token is NOT in the Parent field (already resolved), skip."""
        from nextseek_api.batch_upload.orphan_resolution import resolve_orphans

        mock_conn = MagicMock()
        mock_driver = MagicMock()

        # Parent field already has the UID, not the Name
        orphan_meta = '{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"MUS-260305MIT-1"}'
        mock_fetch = MagicMock()
        mock_fetch.fetchone.return_value = (orphan_meta,)
        mock_conn.execute.return_value = mock_fetch

        orphans = [{
            "id": 500, "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A"],
            "matched_tokens": {"Mouse-A": "MUS-260305MIT-1"},
        }]
        parent_info = {"MUS-260305MIT-1": {"sample_id": 200, "uuid": "MUS-260305MIT-1"}}

        stats = resolve_orphans(
            orphans=orphans, parent_info=parent_info,
            sql_conn=mock_conn, neo4j_driver=mock_driver, neo4j_database="testdb",
        )

        assert stats["resolved"] == 0
        assert stats["edges_created"] == 0

    def test_does_not_modify_parent_titles(self):
        """parent_titles is permanent — resolve_orphans must NOT touch it."""
        from nextseek_api.batch_upload.orphan_resolution import resolve_orphans

        mock_conn = MagicMock()
        mock_driver = MagicMock()

        orphan_meta = '{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"Mouse-A"}'
        mock_fetch = MagicMock()
        mock_fetch.fetchone.return_value = (orphan_meta,)
        sop_result = MagicMock()
        sop_result.fetchone.return_value = None
        mock_conn.execute.side_effect = [mock_fetch, sop_result, MagicMock()]
        mock_driver.execute_query.return_value = MagicMock(records=[])

        orphans = [{
            "id": 500, "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A"],
            "matched_tokens": {"Mouse-A": "MUS-260305MIT-1"},
        }]
        parent_info = {"MUS-260305MIT-1": {"sample_id": 200, "uuid": "MUS-260305MIT-1"}}

        resolve_orphans(
            orphans=orphans, parent_info=parent_info,
            sql_conn=mock_conn, neo4j_driver=mock_driver, neo4j_database="testdb",
        )

        for call in mock_driver.execute_query.call_args_list:
            cypher = str(call)
            assert "parent_titles" not in cypher, "resolve_orphans must not modify parent_titles"

    def test_partial_resolution_keeps_remaining_tokens(self):
        """If 2 unresolved parents and only 1 matches, keep the other in Parent field."""
        from nextseek_api.batch_upload.orphan_resolution import resolve_orphans

        mock_conn = MagicMock()
        mock_driver = MagicMock()

        orphan_meta = '{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"Mouse-A;StillUnresolved"}'
        mock_fetch = MagicMock()
        mock_fetch.fetchone.return_value = (orphan_meta,)
        sop_result = MagicMock()
        sop_result.fetchone.return_value = None
        mock_conn.execute.side_effect = [mock_fetch, sop_result, MagicMock()]
        mock_driver.execute_query.return_value = MagicMock(records=[])

        orphans = [{
            "id": 500, "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A", "StillUnresolved"],
            "matched_tokens": {"Mouse-A": "MUS-260305MIT-1"},
        }]
        parent_info = {"MUS-260305MIT-1": {"sample_id": 200, "uuid": "MUS-260305MIT-1"}}

        stats = resolve_orphans(
            orphans=orphans, parent_info=parent_info,
            sql_conn=mock_conn, neo4j_driver=mock_driver, neo4j_database="testdb",
        )

        assert stats["resolved"] == 1


class TestResolveOrphansTask:
    def test_task_runs_discovery_and_resolution(self):
        from nextseek_api.batch_upload.tasks import resolve_orphans_task

        with patch("nextseek_api.batch_upload.orphan_resolution.discover_orphans") as mock_discover, \
             patch("nextseek_api.batch_upload.orphan_resolution.resolve_orphans") as mock_resolve, \
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
            mock_resolve.return_value = {"resolved": 1, "edges_created": 1}

            mock_conn = MagicMock()
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

            result = resolve_orphans_task(
                identity_map={"X": "Y"},
                parent_info={"Y": {"sample_id": 1, "uuid": "Y"}},
            )

            mock_discover.assert_called_once()
            mock_resolve.assert_called_once()
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
