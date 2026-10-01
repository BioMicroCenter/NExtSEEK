"""
Fixtures for the throwaway-Neo4j graph scope lane.

lane.sh starts a private Neo4j and sets GRAPH_SCOPE_NEO4J_URI and GRAPH_SCOPE_NEO4J_PASSWORD; without both, every
test in this folder skips (the ordinary ai lane runs with no network and reports them skipped). The lane never
reaches any other database.

``Lane.reload`` wipes the private database and loads the fixture fresh in one write transaction, so each caller's run
starts from the same graph; ``Lane.prune`` deletes what a caller cannot see, for the differential oracle; ``Lane.read``
runs a statement in a READ transaction, as the tool does.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 11.2.
"""
from __future__ import annotations

import os
import time

import pytest

from NessieAI.tests.chat_nextseek.graph_scope import fixture_graph

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")
USER = "neo4j"
CONNECT_WAIT_S = 180


class Lane:
    def __init__(self, driver) -> None:
        self.driver = driver

    def reload(self) -> None:
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
            session.execute_write(fixture_graph.load)
            session.run(fixture_graph.FULLTEXT_INDEX).consume()
            session.run("CALL db.awaitIndexes(300)").consume()

    def prune(self, caller: tuple[int, ...]) -> None:
        def work(tx):
            for statement, params in fixture_graph.prune_statements(caller):
                tx.run(statement, params).consume()

        with self.driver.session() as session:
            session.execute_write(work)

    def read(self, cypher: str, params: dict | None = None) -> list[dict]:
        def work(tx):
            return [dict(record) for record in tx.run(cypher, params or {})]

        with self.driver.session() as session:
            return session.execute_read(work)

    def counts(self) -> tuple[int, int]:
        nodes = self.read("MATCH (n) RETURN count(n) AS n")[0]["n"]
        rels = self.read("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        return nodes, rels


@pytest.fixture(scope="session")
def lane():
    if not (URI and PASSWORD):
        pytest.skip("the graph scope lane runs only under lane.sh (GRAPH_SCOPE_NEO4J_URI, GRAPH_SCOPE_NEO4J_PASSWORD)")
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD), notifications_min_severity="OFF")
    deadline = time.monotonic() + CONNECT_WAIT_S
    while True:
        try:
            driver.verify_connectivity()
            break
        except Exception:
            if time.monotonic() > deadline:
                driver.close()
                raise
            time.sleep(2)
    try:
        yield Lane(driver)
    finally:
        driver.close()
