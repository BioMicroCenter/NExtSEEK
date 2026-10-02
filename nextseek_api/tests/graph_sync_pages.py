"""Fake-driver support for ``writer.read_sample_pages``: its page bounds statement and the page and rest forms of a
paged read, answered from the rows a fake gives for the whole-graph read, with Cypher's semantics (a number compares
with the bounds; null, a string, a boolean and NaN fall to the rest).

By default the bounds statement sees one Sample id, +infinity, so every numeric id falls in one page and a fake that
knows only the template answers as it did before paging; pass the fake graph's ids for real pages.
"""
from __future__ import annotations

import math

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import writer

ONE_PAGE = (math.inf,)


def _number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and not (isinstance(value, float) and math.isnan(value)))


def bounds(ids, params) -> list[dict]:
    """``q.SAMPLE_ID_PAGE_END`` over these Sample ids."""
    page = sorted(v for v in ids if _number(v) and v > params["after"])[:params["limit"]]
    return [{"n": len(page), "last": page[-1] if page else None}]


def in_page(value, params, rest: bool) -> bool:
    if rest:
        return not (_number(value) and value > params["after"])
    return _number(value) and params["after"] < value <= params["last"]


def page_answer(query, params, keys: dict, rows_of, ids=ONE_PAGE):
    """The rows for a paging statement, or None when ``query`` is not one. ``keys`` maps each paged template to the
    row key holding its Sample ``c``'s id; ``rows_of(template)`` gives the whole-graph read's rows."""
    if query == q.SAMPLE_ID_PAGE_END:
        return bounds(ids, params)
    for template, key in keys.items():
        page, rest = writer.page_forms(template)
        if query in (page, rest):
            return [row for row in rows_of(template) if in_page(row[key], params, query == rest)]
    return None


def budgeted(transformer, budget: int):
    """``transformer`` behind Neo4j's transaction timeout, measured in records: a read that streams more than
    ``budget`` records into it fails as dev's labels read did (TransactionTimedOutClientConfiguration)."""
    from neo4j.exceptions import ClientError

    def run(records):
        def stream():
            for i, record in enumerate(records):
                if i == budget:
                    raise ClientError("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration: "
                                      f"the read streamed more than {budget} records")
                yield record
        return transformer(stream())
    return run


def paged(responder, keys: dict, ids=ONE_PAGE):
    """Wrap ``responder(query, params)``, which answers each template of ``keys`` as the whole-graph read."""
    def answer(query, params):
        rows = page_answer(query, params, keys, lambda template: list(responder(template, params)), ids)
        return responder(query, params) if rows is None else rows
    return answer
