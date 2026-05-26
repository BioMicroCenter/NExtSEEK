"""Neo4j read-only query tool. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import re

from ...config import ChatConfig


def tool_neo4j_query(config: ChatConfig, cypher: str, parameters: dict | None = None) -> dict:
    """
    Execute a read-only Cypher query against the configured Neo4j instance.
    Returns a structured dict: {ok, data, count, cypher, parameters, counters} on success,
    or {ok: False, error, cypher} on failure. Opens and closes a driver per call.
    """
    try:
        from neo4j import GraphDatabase  # type: ignore
    except ImportError:
        return {"ok": False, "error": "neo4j driver not installed; run 'uv add neo4j'", "data": None, "cypher": cypher}

    if not getattr(config, "NEO4J_PASSWORD", None):
        return {"ok": False, "error": "NEO4J_PASSWORD not configured", "data": None, "cypher": cypher}

    # Block any write/mutating Cypher clauses — allow read-only queries only.
    _WRITE_KEYWORDS = re.compile(
        r"\b(CREATE|MERGE|SET|DELETE|DETACH\s+DELETE|REMOVE|DROP|CALL\s+db\.|CALL\s+apoc\.schema\.|CALL\s+apoc\.periodic\.|LOAD\s+CSV)\b",
        re.IGNORECASE,
    )
    if _WRITE_KEYWORDS.search(cypher):
        print(f"[DEBUG][GRAPHDB] Blocked write query: {cypher!r}")
        return {"ok": False, "error": "Write operations are not permitted; only read (MATCH/RETURN) queries are allowed.", "data": None, "cypher": cypher}

    params = parameters or {}
    driver = None
    try:
        try:
            driver = GraphDatabase.driver(
                config.NEO4J_URI,
                auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
                notifications_min_severity="OFF",
            )
        except TypeError:
            driver = GraphDatabase.driver(
                config.NEO4J_URI,
                auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
            )
        with driver.session(database=getattr(config, "NEO4J_DATABASE", "neo4j")) as db_session:
            result = db_session.run(cypher, params)
            records = [dict(record) for record in result]
            summary = result.consume()
            counters = {}
            if summary and summary.counters:
                try:
                    counters = dict(vars(summary.counters))
                except Exception:
                    pass
            print(f"[DEBUG][GRAPHDB] Query returned {len(records)} records")
            return {
                "ok": True,
                "data": records,
                "count": len(records),
                "cypher": cypher,
                "parameters": params,
                "counters": counters,
            }
    except Exception as e:
        print(f"[DEBUG][GRAPHDB] Query failed: {e!r}")
        return {"ok": False, "error": str(e), "data": None, "cypher": cypher}
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass
