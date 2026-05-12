"""Integration tests for Neo4j relationship creation against a real Neo4j instance.

Skipped if Neo4j is unavailable.
"""
import os
import uuid

import pytest

from nextseek_api.batch_upload.models import InStudyRelRow, OfTypeRelRow
from nextseek_api.batch_upload.neo4j_sync import (
    bulk_merge_in_study_relationships,
    bulk_merge_of_type_relationships,
)

# ── Skip if Neo4j not configured ────────────────────────────────────────────

try:
    from nextseek_api.batch_upload.config import Neo4jConfig

    _cfg = Neo4jConfig.from_django_settings()
    _NEO4J_URI = _cfg.URI or None
    _NEO4J_USER = _cfg.NEO4J_USER or None
    _NEO4J_PASS = _cfg.PASSWORD or None
    _NEO4J_DB = _cfg.NEO4J_DB or "neo4j"
except Exception:
    _NEO4J_URI = _NEO4J_USER = _NEO4J_PASS = _NEO4J_DB = None

_HAS_NEO4J = bool(_NEO4J_URI and _NEO4J_USER and _NEO4J_PASS)

pytestmark = pytest.mark.skipif(not _HAS_NEO4J, reason="Neo4j not configured")


@pytest.fixture(scope="module")
def neo4j_driver():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(_NEO4J_URI, auth=(_NEO4J_USER, _NEO4J_PASS))
    yield driver
    driver.close()


@pytest.fixture()
def temp_sample(neo4j_driver):
    """Create a temporary Sample node with a unique UUID, clean up after test."""
    test_uuid = f"TEST-{uuid.uuid4().hex[:12]}"
    neo4j_driver.execute_query(
        "CREATE (s:Sample {uuid: $uuid, id: 999999})",
        {"uuid": test_uuid},
        database_=_NEO4J_DB,
    )
    yield test_uuid
    neo4j_driver.execute_query(
        "MATCH (s:Sample {uuid: $uuid}) DETACH DELETE s",
        {"uuid": test_uuid},
        database_=_NEO4J_DB,
    )


class TestInStudyIntegration:
    def test_creates_in_study_relationship(self, neo4j_driver, temp_sample):
        # Find an existing Study node
        result = neo4j_driver.execute_query(
            "MATCH (st:Study) RETURN st.id AS id LIMIT 1",
            database_=_NEO4J_DB,
        )
        if not result.records:
            pytest.skip("No Study nodes in Neo4j")
        study_id = result.records[0]["id"]

        rows = [InStudyRelRow(sample_uuid=temp_sample, study_id=study_id)]
        count = bulk_merge_in_study_relationships(neo4j_driver, _NEO4J_DB, rows)
        assert count == 1

        # Verify relationship exists
        verify = neo4j_driver.execute_query(
            "MATCH (s:Sample {uuid: $uuid})-[:IN_STUDY]->(st:Study {id: $sid}) RETURN count(*) AS c",
            {"uuid": temp_sample, "sid": study_id},
            database_=_NEO4J_DB,
        )
        assert verify.records[0]["c"] == 1

    def test_idempotent_merge(self, neo4j_driver, temp_sample):
        result = neo4j_driver.execute_query(
            "MATCH (st:Study) RETURN st.id AS id LIMIT 1",
            database_=_NEO4J_DB,
        )
        if not result.records:
            pytest.skip("No Study nodes in Neo4j")
        study_id = result.records[0]["id"]

        rows = [InStudyRelRow(sample_uuid=temp_sample, study_id=study_id)]
        bulk_merge_in_study_relationships(neo4j_driver, _NEO4J_DB, rows)
        bulk_merge_in_study_relationships(neo4j_driver, _NEO4J_DB, rows)

        verify = neo4j_driver.execute_query(
            "MATCH (s:Sample {uuid: $uuid})-[r:IN_STUDY]->(st:Study {id: $sid}) RETURN count(r) AS c",
            {"uuid": temp_sample, "sid": study_id},
            database_=_NEO4J_DB,
        )
        assert verify.records[0]["c"] == 1  # still 1, not 2


class TestOfTypeIntegration:
    def test_creates_of_type_relationship(self, neo4j_driver, temp_sample):
        result = neo4j_driver.execute_query(
            "MATCH (st:SampleType) RETURN st.id AS id LIMIT 1",
            database_=_NEO4J_DB,
        )
        if not result.records:
            pytest.skip("No SampleType nodes in Neo4j")
        st_id = result.records[0]["id"]

        rows = [OfTypeRelRow(sample_id=999999, sample_uuid=temp_sample, sample_type_id=st_id)]
        count = bulk_merge_of_type_relationships(neo4j_driver, _NEO4J_DB, rows)
        assert count == 1

        verify = neo4j_driver.execute_query(
            "MATCH (s:Sample {uuid: $uuid})-[:OF_TYPE]->(st:SampleType {id: $stid}) RETURN count(*) AS c",
            {"uuid": temp_sample, "stid": st_id},
            database_=_NEO4J_DB,
        )
        assert verify.records[0]["c"] == 1
