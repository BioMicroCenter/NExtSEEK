"""Tests for orphan_resolution module."""
import json

import pytest
from unittest.mock import MagicMock, patch

from django.test import override_settings

from nextseek_api.batch_upload.orphan_resolution import (
    _extract_protocol,
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

        # Verify the SQL UPDATE replaced Mouse-A with UID but kept StillUnresolved
        # Calls: [0]=FETCH metadata, [1]=UPDATE metadata (no Protocol → no SOP lookup)
        update_call = mock_conn.execute.call_args_list[1]  # 2nd call = UPDATE
        update_params = update_call[0][1]
        updated_meta = update_params["meta"]
        assert "MUS-260305MIT-1" in updated_meta, "Mouse-A should be replaced with UID"
        assert "StillUnresolved" in updated_meta, "StillUnresolved should be kept"
        assert "Mouse-A" not in updated_meta, "Mouse-A identity token should be gone"


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


class TestOrphanResolutionIntegration:
    """End-to-end test with mocked MariaDB and Neo4j."""

    def test_full_orphan_resolution_flow(self):
        """Upload orphan → upload parent → verify resolution."""
        from nextseek_api.batch_upload.orphan_resolution import discover_orphans, resolve_orphans

        mock_driver = MagicMock()
        mock_conn = MagicMock()

        # Orphan: child1 with unresolved Parent="Mouse-A" (preserved by Task 1)
        orphan_meta = '{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"Mouse-A","Protocol":"https://fairdata-dev.mit.edu/sops/57"}'

        # New batch: Mouse-A uploaded as MUS-260305MIT-1
        identity_map = {"Mouse-A": "MUS-260305MIT-1"}
        parent_info = {"MUS-260305MIT-1": {"sample_id": 200, "uuid": "MUS-260305MIT-1"}}

        # Mock discovery query result
        orphan_record = MagicMock()
        orphan_record.data.return_value = {
            "id": 500,
            "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A"],
        }
        discover_result = MagicMock()
        discover_result.records = [orphan_record]

        # Mock resolve: SQL fetch + SOP lookup + update
        fetch_result = MagicMock()
        fetch_result.fetchone.return_value = (orphan_meta,)
        sop_result = MagicMock()
        sop_result.fetchone.return_value = ("CD8 Depletion Protocol",)
        mock_conn.execute.side_effect = [fetch_result, sop_result, MagicMock()]

        neo4j_edge_result = MagicMock()
        neo4j_edge_result.records = []
        mock_driver.execute_query.side_effect = [discover_result, neo4j_edge_result]

        # Run discovery
        orphans = discover_orphans(mock_driver, "testdb", identity_map)
        assert len(orphans) == 1
        assert orphans[0]["matched_tokens"] == {"Mouse-A": "MUS-260305MIT-1"}

        # Run resolution
        stats = resolve_orphans(
            orphans=orphans, parent_info=parent_info,
            sql_conn=mock_conn, neo4j_driver=mock_driver, neo4j_database="testdb",
        )

        assert stats["resolved"] == 1
        assert stats["edges_created"] == 1

        # Verify DERIVED_FROM was created
        derived_calls = [c for c in mock_driver.execute_query.call_args_list
                         if "DERIVED_FROM" in str(c)]
        assert len(derived_calls) >= 1

        # Verify parent_titles NOT modified
        for call in mock_driver.execute_query.call_args_list:
            assert "SET s.parent_titles" not in str(call)


class TestResolveOrphansVariantKeys:
    """Test that resolve_orphans reads ALL parent-containing keys."""

    def test_matched_token_in_variant_key_resolved(self):
        """If the matched token is in a variant key (not Parent), it should still be resolved."""
        import json
        from nextseek_api.batch_upload.orphan_resolution import resolve_orphans

        mock_conn = MagicMock()
        mock_driver = MagicMock()

        orphan_meta = '{"UID":"CHD-260101MIT-1","Name":"child1","Treatment1Parent":"Mouse-A"}'
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

        stats = resolve_orphans(
            orphans=orphans, parent_info=parent_info,
            sql_conn=mock_conn, neo4j_driver=mock_driver, neo4j_database="testdb",
        )

        assert stats["resolved"] == 1
        assert stats["edges_created"] >= 1

    def test_variant_key_not_modified_by_resolution(self):
        """Variant keys should NOT be modified — only Parent key is updated."""
        import json
        from nextseek_api.batch_upload.orphan_resolution import resolve_orphans

        mock_conn = MagicMock()
        mock_driver = MagicMock()

        orphan_meta = '{"UID":"CHD-260101MIT-1","Name":"child1","Treatment1Parent":"Mouse-A"}'
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

        # Check the SQL UPDATE call — written meta should still have Treatment1Parent unchanged
        # execute() is called with positional args: execute(sql_text, params_dict)
        for call in mock_conn.execute.call_args_list:
            positional = call[0]
            if len(positional) >= 2 and isinstance(positional[1], dict) and "meta" in positional[1]:
                written_meta = json.loads(positional[1]["meta"])
                assert written_meta.get("Treatment1Parent") == "Mouse-A"
                break

    def test_parent_and_variant_both_contribute_tokens(self):
        """Tokens from Parent + variant key should both be available for matching."""
        import json
        from nextseek_api.batch_upload.orphan_resolution import resolve_orphans

        mock_conn = MagicMock()
        mock_driver = MagicMock()

        orphan_meta = '{"UID":"CHD-260101MIT-1","Name":"child1","Parent":"Unresolved_A","Treatment1Parent":"Mouse-A"}'
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

        stats = resolve_orphans(
            orphans=orphans, parent_info=parent_info,
            sql_conn=mock_conn, neo4j_driver=mock_driver, neo4j_database="testdb",
        )

        assert stats["resolved"] == 1
        assert stats["edges_created"] >= 1


# ── Protocol -> SOP resolution on the orphan path ─────────────────────────


def _protocol_conn(title_rows=None, id_row=None):
    """A sql_conn whose first execute() answers the title lookup and whose
    next answers the id -> title lookup."""
    conn = MagicMock()
    results = []
    if title_rows is not None:
        by_title = MagicMock()
        by_title.fetchall.return_value = title_rows
        results.append(by_title)
    by_id = MagicMock()
    by_id.fetchone.return_value = id_row
    results.append(by_id)
    conn.execute.side_effect = results
    return conn


class TestExtractProtocol:
    """orphan_resolution wrote the same null protocol as neo4j_sync: it only
    understood the ``/sops/<id>`` URL, which production rarely stores."""

    def test_internal_sops_url_still_resolves(self):
        conn = _protocol_conn(id_row=("SOP Five",))
        assert _extract_protocol({"Protocol": "/sops/5"}, conn) == (5, "SOP Five", None)

    def test_bare_title_resolves(self):
        conn = _protocol_conn(
            title_rows=[(7, "P.FOR-200623-V1_x.docx")], id_row=("P.FOR-200623-V1_x.docx",),
        )
        assert _extract_protocol({"Protocol": "P.FOR-200623-V1_x.docx"}, conn) == (
            7, "P.FOR-200623-V1_x.docx", None,
        )

    def test_uid_url_resolves_by_title(self):
        conn = _protocol_conn(
            title_rows=[(7, "P.FOR-200623-V1_x.docx")], id_row=("P.FOR-200623-V1_x.docx",),
        )
        meta = {"Protocol": "http://127.0.0.1:8000/seek/sop/uid=P.FOR-200623-V1_x.docx/"}
        assert _extract_protocol(meta, conn)[0] == 7

    def test_lowercase_protocol_key_is_honoured(self):
        conn = _protocol_conn(title_rows=[(7, "T")], id_row=("T",))
        assert _extract_protocol({"protocol": "T"}, conn)[0] == 7

    def test_unknown_title_reports_the_raw_value(self):
        conn = _protocol_conn(title_rows=[])
        assert _extract_protocol({"Protocol": "No Such SOP"}, conn) == (
            None, None, "No Such SOP",
        )

    def test_foreign_url_never_yields_a_local_id(self):
        with override_settings(
            SEEK_PUBLIC_URL="http://localhost:3000",
            SEEK_URL="http://seek:3000",
            ALLOWED_HOSTS=["127.0.0.1"],
        ):
            conn = _protocol_conn(title_rows=[])
            sop_id, title, unresolved = _extract_protocol(
                {"Protocol": "https://fairdomhub.org/sops/795"}, conn
            )
        assert (sop_id, title) == (None, None)
        assert unresolved == "https://fairdomhub.org/sops/795"

    def test_absent_protocol_is_not_reported_as_unresolved(self):
        conn = MagicMock()
        assert _extract_protocol({}, conn) == (None, None, None)
        conn.execute.assert_not_called()


class TestResolveOrphansProtocolReporting:
    """An unresolvable Protocol must show up in the task's own stats."""

    @staticmethod
    def _resolve(protocol, title_rows):
        conn = MagicMock()
        fetch = MagicMock()
        fetch.fetchone.return_value = (
            json.dumps({"Parent": "Mouse-A", "Protocol": protocol}),
        )
        by_title = MagicMock()
        by_title.fetchall.return_value = title_rows
        by_id = MagicMock()
        by_id.fetchone.return_value = ("SOP",)
        # fetch metadata, [title lookup], [id -> title], update metadata
        results = [fetch, by_title, by_id, MagicMock()]
        conn.execute.side_effect = results

        orphans = [{
            "id": 500,
            "uuid": "CHD-260101MIT-1",
            "parent_titles": ["Mouse-A"],
            "matched_tokens": {"Mouse-A": "MUS-260305MIT-1"},
        }]
        return resolve_orphans(
            orphans=orphans,
            parent_info={"MUS-260305MIT-1": {"sample_id": 1, "uuid": "MUS-260305MIT-1"}},
            sql_conn=conn,
            neo4j_driver=MagicMock(),
            neo4j_database="nextseekdev",
        )

    def test_unresolved_protocol_is_counted(self):
        stats = self._resolve("No Such SOP", title_rows=[])
        assert stats["protocols_unresolved"] == 1
        assert stats["edges_created"] == 1

    def test_resolved_protocol_is_not_counted(self):
        stats = self._resolve("Known SOP", title_rows=[(7, "Known SOP")])
        assert stats["protocols_unresolved"] == 0
