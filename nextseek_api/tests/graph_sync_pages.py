"""Fake-driver support for ``writer.read_sample_pages``: its page bounds statement and the page and rest forms of any
paged read, answered from the rows a fake gives for the template (the whole-graph read), with Cypher's semantics (a
number compares with the bounds; null, a string, a boolean and NaN fall to the rest). A row's page is decided by the
value under the alias the template returns ``c.id`` as.

By default the bounds statement sees one Sample id, +infinity, so every numeric id falls in one page and a fake that
knows only the template answers as it did before paging; pass the fake graph's ids for real pages.
"""
from __future__ import annotations

import math
import re

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import writer

ONE_PAGE = (math.inf,)
_ALIAS = re.compile(r"\bc\.id AS (\w+)")


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


def template_of(query) -> str:
    """The paged template a page or rest form was made from; any other statement is its own."""
    for where in (writer._PAGE, writer._REST):
        if where in query:
            return query.replace(where, "{page}")
    return query


def page_answer(query, params, rows_of, ids=ONE_PAGE):
    """The rows for a paging statement, or None when ``query`` is not one. ``rows_of(template)`` gives the
    template's rows as the whole-graph read."""
    if query == q.SAMPLE_ID_PAGE_END:
        return bounds(ids, params)
    template = template_of(query)
    if template == query:
        return None
    key = _ALIAS.search(template).group(1)
    return [row for row in rows_of(template) if in_page(row[key], params, writer._REST in query)]


def budgeted(transformer, budget: int):
    """``transformer`` behind Neo4j's transaction timeout, measured in records: a read that streams more than
    ``budget`` records into it fails as dev's labels read did (TransactionTimedOutClientConfiguration)."""
    def run(records):
        def stream():
            for i, record in enumerate(records):
                if i == budget:
                    raise timed_out(budget)
                yield record
        return transformer(stream())
    return run


def timed_out(budget: int):
    from neo4j.exceptions import ClientError

    return ClientError("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration: "
                       f"the read streamed more than {budget} records")


def paged(responder, ids=ONE_PAGE, budget=None, on=()):
    """Wrap ``responder(query, params)``, which answers each paged template as the whole-graph read. With
    ``budget``, a read of a statement in ``on`` (run whole, or as one of its pages) that answers more rows than that
    fails as a timed-out transaction. A responder wrapped already is returned as it is."""
    if getattr(responder, "paged", False):
        return responder

    def answer(query, params):
        rows = page_answer(query, params, lambda template: list(responder(template, params)), ids)
        rows = list(responder(query, params)) if rows is None else rows
        if budget is not None and template_of(query) in on and len(rows) > budget:
            raise timed_out(budget)
        return rows

    answer.paged = True
    return answer
