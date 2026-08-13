"""Neo4j read-only query tool. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import re

from ...config import ChatConfig


# A trailing `[SKIP n] LIMIT n`, which is the shape the graph prompt asks for.
_TRAILING_LIMIT_RE = re.compile(
    r"\s+(?:SKIP\s+(?:\d+|\$\w+)\s+)?LIMIT\s+(\d+|\$\w+)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def split_trailing_limit(cypher: str, parameters: dict | None = None) -> "tuple[str | None, int | None]":
    """
    Return ``(body_without_limit, effective_limit)`` for a query ending in LIMIT.

    ``(None, None)`` when there is no trailing LIMIT, or when it is a parameter that
    is not bound to an integer.
    """
    if not cypher:
        return None, None
    m = _TRAILING_LIMIT_RE.search(cypher)
    if not m:
        return None, None
    token = m.group(1)
    if token.startswith("$"):
        value = (parameters or {}).get(token[1:])
    else:
        value = token
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None, None
    return cypher[: m.start()], limit


def _probe_total(db_session, body: str, params: dict) -> int | None:
    """
    Count the rows the query WOULD have returned without its LIMIT.

    Raising the cap (250 -> 5000) only moved the wall: graph.tissue_cell_impact's
    legitimate answer is 10,688, so it can never fit under any sane limit. The
    answer is to report the true total alongside a capped preview.

    The ``CALL () { }`` wrap preserves DISTINCT and ORDER BY without having to parse
    the projection. The body is a prefix of an already write-checked query, so this
    introduces no new write surface.
    """
    probe = f"CALL () {{\n{body}\n}}\nRETURN count(*) AS __total"
    record = db_session.run(probe, params).single()
    return int(record["__total"]) if record and record.get("__total") is not None else None


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

            # `count` is len(records) and always has been, so a query that hit its
            # LIMIT reported the limit as if it were the answer. Probe for the real
            # total instead of raising the cap again.
            body, effective_limit = split_trailing_limit(cypher, params)
            total: int | None = len(records)
            truncated = False
            if effective_limit is not None and len(records) >= effective_limit:
                truncated = True
                total = None
                try:
                    total = _probe_total(db_session, body, params)
                    print(f"[DEBUG][GRAPHDB] Result hit LIMIT {effective_limit}; true total = {total}")
                except Exception as probe_err:
                    # Best effort: an unknown total is still more information than a
                    # capped count presented as complete.
                    print(f"[DEBUG][GRAPHDB] Total probe failed: {probe_err!r}")

            return {
                "ok": True,
                "data": records,
                "count": len(records),
                "total": total,
                "truncated": truncated,
                "limit": effective_limit,
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
