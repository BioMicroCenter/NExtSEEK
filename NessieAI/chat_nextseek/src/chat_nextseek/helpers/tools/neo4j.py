"""
Neo4j read-only query tool. Moved from helpers.py during the Phase 2 src/ restructure.

Read-only twice over (spec D3): the text check (`cypher_text.write_clause`, on masked
text) refuses a write before a driver opens, and every statement runs in a READ
transaction (`session.execute_read`) with a timeout, so the server refuses a write the
check misses. Never call `session.run` here: it is an auto-commit transaction in the
default WRITE access mode.

Every statement is also held to the caller's project scope, which rides on the config as a
`GraphScope` (`graph_scope.py`). In order: the write check; the scope (none refuses, before a
driver opens); the prover (`cypher_scope.scope_cypher`: an admin's text runs unchanged, anyone
else's runs with the scope inserted, and what cannot be proven is refused before a driver
opens); the READ transaction and total probe on the statement the prover returned; and, for a
caller who is not an admin, `strip_hidden` over the rows. Every result names the statement
that ran (`cypher`), the one submitted (`submitted_cypher`), the parameters that ran and the
scope decision (`scope`). Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md
section 6.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ...config import ChatConfig
from ...cypher_scope import Refused, scope_cypher, strip_hidden
from ...cypher_text import write_clause
from ...graph_scope import GraphScope, scope_of

# Server-side transaction timeout, seconds, for the query and for its total probe.
QUERY_TIMEOUT_S = 60

_WRITE_REFUSED = "Write operations are not permitted; only read (MATCH/RETURN) queries are allowed."
SCOPE_REFUSED = "This graph query could not be confirmed to stay within your projects, so it was not run."
NO_SCOPE_REFUSED = "No project scope is set for this request, so no graph query can run."


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


def _read_rows(tx, cypher: str, params: dict) -> "tuple[list[dict], dict]":
    """Transaction function: the rows and the counters, read before the transaction closes."""
    result = tx.run(cypher, params)
    records = [dict(record) for record in result]
    summary = result.consume()
    counters = {}
    if summary and summary.counters:
        try:
            counters = dict(vars(summary.counters))
        except Exception:
            pass
    return records, counters


def _read_total(tx, probe: str, params: dict) -> int | None:
    """Transaction function: the probe's single `__total`."""
    record = tx.run(probe, params).single()
    return int(record["__total"]) if record and record.get("__total") is not None else None


def _probe_total(db_session, body: str, params: dict, work=None) -> int | None:
    """
    Count the rows the query WOULD have returned without its LIMIT.

    Raising the cap (250 -> 5000) only moved the wall: graph.tissue_cell_impact's
    legitimate answer is 10,688, so it can never fit under any sane limit. The
    answer is to report the true total alongside a capped preview.

    The ``CALL () { }`` wrap preserves DISTINCT and ORDER BY without having to parse
    the projection. The body is a prefix of an already write-checked query, so this
    introduces no new write surface. It runs in its own READ transaction; ``work``
    is ``_read_total`` wrapped with the timeout.
    """
    probe = f"CALL () {{\n{body}\n}}\nRETURN count(*) AS __total"
    return db_session.execute_read(work or _read_total, probe, params)


def matched_nothing(result: dict | None) -> bool:
    """Did this successful query find nothing? Row count alone does not answer that.

    A query written as `RETURN count(s) AS total` that matches no samples comes back as ONE
    row holding zero, so `count` is 1 and a row-count test calls it a hit. Measured on the
    2026-09-16 evaluation run: of the five graph answers that were a wrong zero, three were
    that shape (`search.hela_trap`, `how_many_samples_are_from_the_ka`,
    `routing.lab_ooc_kamm_count`) and the zero-row retry never fired on any of them. The
    user was told "there are no HeLa samples" when there are four.

    So: no rows, or exactly one row whose numeric values are all zero. The single-row test
    is deliberately narrow. An aggregate answer is one row; a row of real data that happens
    to hold a zero (a count of 0 beside a name, say) keeps its non-numeric values, and a
    query returning several rows has found something whatever the numbers say.

    A genuine zero re-queried is not a loss: the retry prompt tells the model to return the
    same query when the filters are real, the second zero leaves the first result standing,
    and the user gets the answer with the evidence that it was checked twice.
    """
    if not result or not result.get("ok"):
        return False
    count = result.get("count") or 0
    if not count:
        return True
    if count != 1:
        return False
    rows = result.get("data") or []
    if len(rows) != 1 or not isinstance(rows[0], dict):
        return False
    numbers = [v for v in rows[0].values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers or len(numbers) != len(rows[0]):
        return False
    return all(n == 0 for n in numbers)


def is_scope_refusal(result: Any) -> bool:
    """Was this statement refused for its project scope (no scope, or not provable)?

    True exactly when ``result["scope"]["decision"] == "refused"``. A write is refused by the
    write check before the scope is read, and its decision is ``"not_checked"``, so it is not a
    scope refusal: the graph turn keeps today's retry for it instead of falling back.
    """
    if not isinstance(result, dict):
        return False
    scope = result.get("scope")
    return isinstance(scope, dict) and scope.get("decision") == "refused"


def _scope_record(decision: str, scope: GraphScope | None, *, injected=(), joined=(), codes=(),
                  reasons=()) -> dict:
    record: dict[str, Any] = {"decision": decision, "source": scope.source if scope is not None else None}
    if scope is not None and not scope.is_admin:
        record["project_ids"] = list(scope.project_ids)
    record.update(injected=list(injected), joined=list(joined), codes=list(codes), reasons=list(reasons))
    return record


def _failure(error: str, *, ran: Any, submitted: Any, parameters: Any, scope: dict) -> dict:
    return {"ok": False, "error": error, "data": None, "cypher": ran, "submitted_cypher": submitted,
            "parameters": parameters, "scope": scope}


def tool_neo4j_query(config: ChatConfig, cypher: str, parameters: dict | None = None) -> dict:
    """
    Execute a read-only Cypher query against the configured Neo4j instance, held to the
    config's project scope.
    Returns a structured dict: {ok, data, count, total, truncated, limit, cypher, submitted_cypher,
    parameters, counters, scope} on success, or {ok: False, error, data, cypher, submitted_cypher,
    parameters, scope} on failure. `cypher` is the statement that ran (after the scope was
    inserted), or the submitted text when nothing ran. Opens and closes a driver per call. The
    query and its total probe each run in a READ transaction with a QUERY_TIMEOUT_S timeout.
    """
    # A mapping is copied; anything else is handed to the prover as it came, which refuses it.
    submitted_params = dict(parameters) if isinstance(parameters, Mapping) else (parameters or {})

    # Refuse writes before anything else: a refused statement never opens a driver.
    clause = write_clause(cypher, extra_procedures=getattr(config, "EXTRA_ALLOWED_PROCEDURES", ()))
    if clause is not None:
        print(f"[DEBUG][GRAPHDB] Blocked write query ({clause}): {cypher!r}")
        return _failure(
            f"{_WRITE_REFUSED} Refused: {clause}.", ran=cypher, submitted=cypher, parameters=submitted_params,
            scope=_scope_record("not_checked", scope_of(config), codes=("write",),
                                reasons=(f"write check: {clause}",)),
        )

    # No scope on the config: refuse, before a driver opens. Nothing here may read the graph
    # for a caller it cannot name.
    scope = scope_of(config)
    if scope is None:
        print("[DEBUG][GRAPHDB] Refused: no project scope on this config")
        return _failure(
            NO_SCOPE_REFUSED, ran=cypher, submitted=cypher, parameters=submitted_params,
            scope=_scope_record("refused", None, codes=("no_scope",),
                                reasons=("the request carries no project scope",)),
        )

    outcome = scope_cypher(cypher, submitted_params, scope)
    if isinstance(outcome, Refused):
        print(f"[DEBUG][GRAPHDB] Refused for scope {list(outcome.codes)}: {cypher!r}")
        return _failure(
            f"{SCOPE_REFUSED} Reasons: {'; '.join(outcome.reasons)}.", ran=cypher, submitted=cypher,
            parameters=submitted_params,
            scope=_scope_record("refused", scope, codes=outcome.codes, reasons=outcome.reasons),
        )
    ran = outcome.cypher
    params = outcome.parameters
    scope_info = _scope_record(outcome.decision, scope, injected=outcome.injected, joined=outcome.joined)

    def failed(error: str) -> dict:
        return _failure(error, ran=ran, submitted=cypher, parameters=params, scope=scope_info)

    try:
        from neo4j import GraphDatabase, unit_of_work  # type: ignore
    except ImportError:
        return failed("neo4j driver not installed; run 'uv add neo4j'")

    if not getattr(config, "NEO4J_PASSWORD", None):
        return failed("NEO4J_PASSWORD not configured")

    timed = unit_of_work(timeout=QUERY_TIMEOUT_S)
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
            records, counters = db_session.execute_read(timed(_read_rows), ran, params)
            print(f"[DEBUG][GRAPHDB] Query returned {len(records)} records")

            # `count` is len(records) and always has been, so a query that hit its
            # LIMIT reported the limit as if it were the answer. Probe for the real
            # total instead of raising the cap again. The probe wraps the statement
            # that ran, so it carries the same scope.
            body, effective_limit = split_trailing_limit(ran, params)
            total: int | None = len(records)
            truncated = False
            if effective_limit is not None and len(records) >= effective_limit:
                truncated = True
                total = None
                try:
                    total = _probe_total(db_session, body, params, timed(_read_total))
                    print(f"[DEBUG][GRAPHDB] Result hit LIMIT {effective_limit}; true total = {total}")
                except Exception as probe_err:
                    # Best effort: an unknown total is still more information than a
                    # capped count presented as complete.
                    print(f"[DEBUG][GRAPHDB] Total probe failed: {probe_err!r}")

            if not scope.is_admin:
                # A whole node can still be returned; its hidden properties never leave here.
                records = strip_hidden(records)

            return {
                "ok": True,
                "data": records,
                "count": len(records),
                "total": total,
                "truncated": truncated,
                "limit": effective_limit,
                "cypher": ran,
                "submitted_cypher": cypher,
                "parameters": params,
                "counters": counters,
                "scope": scope_info,
            }
    except Exception as e:
        print(f"[DEBUG][GRAPHDB] Query failed: {e!r}")
        return failed(str(e))
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass
