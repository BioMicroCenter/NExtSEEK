"""The follow-up agent: a tool loop where the memory branch used to be a dead end.

``mode == "ask_about_last_results"`` was terminal. It picked one stored bundle, handed
it to the memory agent, and returned; there was no path from that branch back to the
graph. So a follow-up that needed data the stored result could not contain was answered
from the stored result anyway. The production review calls this the second largest
cause of bad answers, 10 of 53:

* tasks 439/440 — after 129 IMPACT patients: "do any of those also have RNA
  sequencing data?" got "it is not possible to determine", and "what other types of
  data are available?" got "No other data types are available". Twenty minutes later
  the same user asked it as a fresh question and got nine downstream data types. 440
  told the user something false.
* tasks 117/118/119 — the stored result held 20 rows of 250, so the follow-up answered
  from 20, explained the 20 as paging, and exported 20.
* tasks 427/428 — "which labs are those mouse samples from?" after a count-only
  query, which kept no rows at all.

Nothing was forgotten in any of those. The UIDs were on disk. What was missing was the
ability to decide to run another query with them, so this module gives the model its
tools and lets it choose:

* ``read_stored_result`` — what the stored bundle holds, and what it does NOT: the row
  count against the real total, whether it was capped, how many UIDs are available, and
  the query that produced it (``stored_query``). When the stored copy is complete, also
  its rows (50 or fewer) or a per-column summary (more), so a follow-up those rows
  answer needs no new query.
* ``run_new_query`` — re-run against the graph seeded with those UIDs.
  When the stored copy is capped or kept no UIDs, the set is rebuilt from
  ``stored_query`` if there is one it can be rebuilt from; when there is not, the
  query covers every matching sample and its ``scope_note`` says so. The query is
  retried as a graph turn's is, and its result carries the graph reviewer's
  ``review``, with two checks of the user's words against the stored result
  (``premise`` and ``binding``).
* ``compute_over_rows``, offered when the caller passes a ``compute`` seam: filter, count
  and break down rows already in hand (the stored rows, or every row of the loop's last
  query) without a new query. It refuses when those rows are not the whole set or lack a
  column it names, and its result carries a review of what its filters matched
  (``agents/followup_compute.py``, ``graph_review.review_compute``).
* ``answer`` — finish, with any caveats as a required field rather than an instruction.

``read_stored_result`` returns counts and a handful of examples, and the stored rows only
when the stored copy is complete (``_stored_contents``): a capped copy's rows are not the
set, so a follow-up about it has to query. ``run_new_query`` returns the head of its
rows, bounded by ``preview_rows``. It used to return counts only, and on the production
acceptance run of 2026-09-22 (task 0006a373) three seeded queries each returned the 23
downstream types of 1,641 NDMA mice as ``{type, n}`` rows; the model was handed "count:
23" and no type name, and replied that "the individual type names were not returned".
A tool loop re-sends its whole conversation on each iteration, so the preview is capped
far below the chatter's (``_graph_rows_for_writer``): a breakdown fits whole, a record
list shows its head, and the turn attaches every row as a file.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from ..artifacts import load_api_result_full, load_memory_payload
from ..config import ChatConfig
from ..graph_scope import RESERVED_PREFIX
from ..llm_clients import LLMFatalError
from ..tool_loop import call_tools

FOLLOWUP_AGENT_KEY = "followup"

#: Generate -> look -> decide, bounded. Each iteration is a model call, and a follow-up
#: that cannot finish in three has misunderstood the question rather than run short.
MAX_ITER = 6

#: How many example identifiers a tool result carries. Enough for the reply to quote
#: some verbatim, small enough that re-sending it every iteration costs nothing.
UID_SAMPLE = 5

#: What ``read_stored_result`` says about a capped copy whose query can be rebuilt. It used
#: to say "run a new query seeded with the UIDs", which scoped the new query to the capped
#: part of the set. ``CAPPED_NOTE_SCOPING`` follows it because the tool's switch is still
#: called ``seed_uids``: read literally, "do not seed" would turn scoping off altogether.
CAPPED_NOTE = (
    "The stored copy holds fewer rows than the total. For a question about the whole set, "
    "run a new query that repeats the stored query's filters (stored_query) and adds the new "
    "condition; do not seed with the stored UIDs."
)
CAPPED_NOTE_SCOPING = (
    " Keep seed_uids true: on a capped result it rebuilds the set from stored_query instead "
    "of binding the stored UIDs."
)
#: The capped note when there is no query to rebuild from (a REST result, or a follow-up's
#: own query, which needs UIDs its bundle no longer holds): unchanged, and the new query's
#: scope_note says it covers only the stored UIDs.
CAPPED_NOTE_SEEDED = (
    "The stored copy holds fewer rows than the total, so it cannot answer a "
    "question about the whole set. Run a new query seeded with the UIDs."
)


#: How many of a new query's rows the model is shown, and the most characters they may
#: take. A breakdown by type or lab is a few dozen short rows and fits whole; a list of
#: sample records shows its head. Re-sent on every later iteration, so kept small.
FOLLOWUP_ROWS_MAX = 50
FOLLOWUP_ROWS_CHARS = 6_000

#: A complete stored copy with more than ``FOLLOWUP_ROWS_MAX`` rows is shown as a summary
#: per column: this many of its most common values, within the same character budget as
#: a rows preview.
COLUMN_TOP = 5
COLUMN_SUMMARY_CHARS = FOLLOWUP_ROWS_CHARS


def preview_rows(rows: Any) -> list:
    """The head of ``rows``, at most ``FOLLOWUP_ROWS_MAX`` of them and ``FOLLOWUP_ROWS_CHARS``
    of compact JSON, in order. Always at least the first row when there is one."""
    shown: list = []
    size = 2
    for row in list(rows or [])[:FOLLOWUP_ROWS_MAX]:
        size += len(json.dumps(row, separators=(",", ":"), default=str)) + 1
        if shown and size > FOLLOWUP_ROWS_CHARS:
            break
        shown.append(row)
    return shown


def _summary_value(value: Any) -> tuple[tuple, Any]:
    """``(key, value shown)`` for one non-null cell of ``column_summary``.

    The key counts and orders: ranked by kind first, so a column mixing numbers and text
    still sorts, and so True, 1 and "1" stay three values. A list or dict is counted and
    shown by its serialisation with sorted keys, so key order does not make two values
    different.
    """
    if isinstance(value, bool):
        return (1, int(value)), value
    if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)):
        return (2, value), value
    if isinstance(value, str):
        return (3, value), value
    shown = (json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
             if isinstance(value, (list, tuple, dict, float)) else str(value))
    return (4, shown), shown


def _column_summary(rows: list) -> tuple[dict[str, dict], int]:
    """``({column: {distinct, nulls, top}}, columns left out)`` over every row of ``rows``.

    ``distinct`` and ``top`` are over non-null values only: ``top`` is the ``COLUMN_TOP``
    most common as ``[value, count]``, ties ordered by value. ``nulls`` counts the rows where
    the column is null or missing, so "10 labs, and one row with no lab" reads as 10 labs,
    not 11, and every column still accounts for every row. Columns go in the order they
    first appear; one that does not fit in ``COLUMN_SUMMARY_CHARS`` is left out whole rather
    than cut, and counted.
    """
    records = [row for row in rows if isinstance(row, dict)]
    columns = list(dict.fromkeys(key for row in records for key in row))
    summary: dict[str, dict] = {}
    omitted = 0
    size = 2
    for column in columns:
        counts: dict[tuple, int] = {}
        shown: dict[tuple, Any] = {}
        nulls = 0
        for row in records:
            cell = row.get(column)
            if cell is None:
                nulls += 1
                continue
            key, value = _summary_value(cell)
            counts[key] = counts.get(key, 0) + 1
            shown.setdefault(key, value)
        ranked = sorted(counts, key=lambda k: (-counts[k], k))[:COLUMN_TOP]
        entry = {"distinct": len(counts), "nulls": nulls,
                 "top": [[shown[k], counts[k]] for k in ranked]}
        cost = len(json.dumps({column: entry}, separators=(",", ":"), default=str))
        if size + cost > COLUMN_SUMMARY_CHARS:
            omitted += 1
            continue
        summary[column] = entry
        size += cost
    return summary, omitted


def _stored_contents(rows: list, *, capped: bool) -> dict[str, Any]:
    """What a complete stored copy holds, for the loop to answer from without a new query.

    ``capped`` is ``describe_stored_result``'s own flag, the one its note uses: a capped
    copy gets nothing here, because its rows are not the set. Otherwise 50 rows or fewer
    are shown as rows, bounded as ``preview_rows`` bounds a new query's, and a cut list
    says so; more rows are shown as ``column_summary``.
    """
    if capped or not rows:
        return {}
    if len(rows) <= FOLLOWUP_ROWS_MAX:
        shown = preview_rows(rows)
        return {"rows": shown, "rows_shown": len(shown), "rows_truncated": len(shown) < len(rows)}
    summary, omitted = _column_summary(rows)
    if not summary and not omitted:
        return {}
    return {"column_summary": summary, "columns_omitted": omitted}


#: A NExtSEEK reply carries a fenced debug block. On turn 1147 `read_stored_result`
#: returned ~1,000 tokens, mostly that block, and it is re-sent on every later iteration.
_DEBUG_MARKER = "**Debug info**"


def _without_debug_block(reply: Any) -> str | None:
    """The user-facing half of an earlier reply."""
    if not isinstance(reply, str) or not reply:
        return reply if reply is None else ""
    at = reply.find(_DEBUG_MARKER)
    return (reply[:at] if at > 0 else reply).strip()


#: The reply when the loop failed, had no tool surface, or ended with nothing to say.
FOLLOWUP_UNAVAILABLE_REPLY = ("I could not finish this follow-up. Ask it as a fresh question "
                              "and I will run it properly.")

#: The end of a reply made from what the loop found when it ran out of turns.
_PARTIAL = ("I ran out of steps before I could finish, so treat this as partial: "
            "ask it again and I will answer it properly.")


def _reply_from_computes(computes) -> str | None:
    """The last successful computation's count, as a partial reply, or None when none succeeded."""
    done = [c for c in computes or () if isinstance(c, dict) and isinstance(c.get("result"), dict)
            and c["result"].get("ok") is True and _is_count(c["result"].get("count"))]
    if not done:
        return None
    last = done[-1]
    n = last["result"]["count"]
    rows = ("the rows the follow-up query returned" if last.get("source") == "last_query"
            else "the earlier result's rows")
    if n == 0:
        return f"None of {rows} show this, which does not mean that none exist. {_PARTIAL}"
    return f"{n:,} of {rows} match. {_PARTIAL}"


def _reply_from_queries(queries: list[dict], computes=()) -> str | None:
    """What the loop established, when it ran out of turns before saying it.

    Worse than an answer the model composed. The stored-result answer this branch used
    before could not see what these queries returned, and on turn 1147 reported its absence.
    A query that found something comes first; failing that, the last computation over rows
    in hand that succeeded.
    """
    ran = [q for q in queries or [] if isinstance(q, dict)]
    found = [q for q in ran if (q.get("result") or {}).get("ok") and (q.get("result") or {}).get("count")]
    if not found:
        computed = _reply_from_computes(computes)
        if computed:
            return computed
        if ran:
            return ("I could not finish checking this. The follow-up query I ran did not come back with "
                    "anything I can stand behind, so ask it as a fresh question and I will run it properly.")
        if computes:
            return ("I could not finish checking this. The computation I ran over the earlier result did "
                    "not succeed, so ask it as a fresh question and I will run it properly.")
        return None
    last = found[-1]
    result = last["result"]
    examples = [str(e) for e in (result.get("examples") or [])][:5]
    parts = [f"{result['count']:,} records match, from a follow-up query over the previous result."
             if isinstance(result.get("count"), int) else "The follow-up query found records."]
    if examples:
        parts.append("Examples: " + ", ".join(examples) + ".")
    parts.append(_PARTIAL)
    return " ".join(parts)


def resolve_followup_outcome(outcome: dict | None) -> str:
    """The follow-up turn's reply: the loop's own, what its work established, or the fixed sentence.

    The whole of turn 1147's defect is that the caller had only ``if reply:`` to tell
    three completed graph queries from a profile with no tool surface, so it answered a
    lineage question from a five-column bundle and asserted the absence of what the
    queries had found. Nothing answers from the stored snapshot any more (n0914-1175): a
    reply keeps its caveats; queries or computations without a reply give what they
    established; a failed loop (``None``), a profile with no tool surface
    (``unsupported``) and a loop that ended with nothing give
    ``FOLLOWUP_UNAVAILABLE_REPLY``.
    """
    if not outcome or outcome.get("unsupported"):
        return FOLLOWUP_UNAVAILABLE_REPLY
    reply = outcome.get("reply")
    if reply:
        caveats = [str(c) for c in (outcome.get("caveats") or []) if str(c).strip()]
        if caveats:
            reply = reply + "\n\n" + "\n".join(f"- {c}" for c in caveats)
        return reply
    if outcome.get("queries") or outcome.get("computes"):
        return (_reply_from_queries(outcome.get("queries") or [], computes=outcome.get("computes") or ())
                or FOLLOWUP_UNAVAILABLE_REPLY)
    return FOLLOWUP_UNAVAILABLE_REPLY


def build_followup_tool_schemas(*, final: bool = False, compute: bool = False) -> list[dict]:
    """The tools, in the order a follow-up naturally uses them. ``compute_over_rows`` is
    offered only when ``compute`` is true: the caller has a seam that runs it.

    ``final`` offers only ``answer``, which is what the extra terminal pass in
    ``run_followup`` uses. Restricting the SIXTH iteration this way was tried first and
    was not enough: on 2026-09-22 the model called ``run_new_query`` on that iteration
    anyway, the dispatch ran it because it never checked what had been offered, and the
    turn was spent. The loop now keeps its six working iterations and takes one extra
    pass that can only answer, with the refusal enforced in the dispatch.
    """
    schemas = [
        {
            "name": "read_stored_result",
            "description": (
                "Read what the previous result actually holds. Returns its total, how "
                "many rows were stored, whether the stored rows were capped, how many "
                "UIDs are available, a few example UIDs, the query that produced it "
                "(stored_query; null when the result did not come from the graph), and "
                "whether a new query can be rebuilt from that query "
                "(stored_query_rebuildable). When the stored copy is complete (capped "
                "is false), it also returns what the copy holds: the rows themselves "
                f"(rows) when there are {FOLLOWUP_ROWS_MAX} or fewer, or else "
                "column_summary, which gives each column's number of distinct non-null "
                "values (distinct), how many rows have it null or missing (nulls), and "
                f"its {COLUMN_TOP} most common non-null values with their counts (top), "
                "over every stored row. Answer from rows or column_summary without a new "
                "query when they hold the answer. When total_known is false, no total was "
                "stored, so such an answer covers the rows_stored stored rows, not the "
                "whole set, and must say so. If rows_truncated is "
                "true, only rows_shown of the rows fit; if a column's distinct is larger "
                "than its top list, its other values are not shown; columns_omitted "
                "counts columns left out for length. When the copy is capped, neither "
                "rows nor column_summary is present. If rows_stored is less than total, "
                "the stored copy cannot answer a question about the whole set and you "
                "must run a new query."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "bundle_id": {
                        "type": "integer",
                        "description": "Which stored result to read. Omit for the one this follow-up is about.",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "run_new_query",
            "description": (
                "Run a NEW query against the graph, scoped to the previous result, and "
                "get back its count and its rows (the first "
                f"{FOLLOWUP_ROWS_MAX} at most: `rows`, with `rows_shown` of "
                "`rows_returned`). Use this whenever the question needs data the stored "
                "result cannot contain: a different data type, a property that was not "
                "selected, or anything about rows beyond the stored ones. Answer from "
                "the rows: when they are a breakdown, name each value and its count. "
                "The result's seed_mode says how it was scoped, and scope_note, when "
                "present, says what that means for the answer. Its review checks the "
                "result the way a graph answer is checked: when the review's verdict is "
                "not ok, its disclosure says what the result matched, or that the user's "
                "number or referent differs from the stored result. Say it in the answer."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "The question to run, written out in full as a standalone "
                            "question. Name the entities; do not write 'those' or 'them'."
                        ),
                    },
                    "seed_uids": {
                        "type": "boolean",
                        "description": (
                            "True to scope the query to the previous result. "
                            "This is what makes 'of those, how many...' mean the same "
                            "set the user is asking about, so keep it true for any "
                            "question about those records. When the stored copy holds "
                            "every UID, they are bound as $uids. When it is capped or "
                            "kept no UIDs and read_stored_result says "
                            "stored_query_rebuildable is true, the set is rebuilt from "
                            "stored_query instead. When that flag is false, a capped "
                            "copy is scoped to the UIDs it holds, which is only part of "
                            "the set, and a copy that kept no UIDs cannot be scoped; the "
                            "result's scope_note says which."
                        ),
                    },
                },
                "required": ["question"],
            },
        },
        {
            "name": "compute_over_rows",
            "description": (
                "Compute over rows you already have, without a new query: the stored result's rows (source "
                "'stored') or every row of the last query you ran this turn (source 'last_query'; you were shown "
                f"at most {FOLLOWUP_ROWS_MAX}). For a count after a filter, a breakdown by a column, values matching "
                "a pattern, or numbers over a column. `where` filters rows (AND); `group_by` counts rows per value of "
                "one or two columns; `code` is optional Python over `rows` (after `where`) that assigns `result`. It "
                "refuses when the rows are not the whole set or lack a column you named: then use run_new_query. A "
                "zero here only means these rows do not show it. A row with no value in a group_by column is in no "
                "group and is counted in group_nulls. When the result has scope_note, follow it. Its review checks "
                "what the filters matched: when its verdict is not ok, say its disclosure in the answer."),
            "input_schema": {"type": "object", "properties": {
                "source": {"type": "string", "enum": ["stored", "last_query"]},
                "where": {"type": "array", "items": {"type": "object", "properties": {
                    "column": {"type": "string",
                               "description": "a column of the rows; json_metadata.<Field> reads a metadata field"},
                    "op": {"type": "string", "enum": ["equals", "contains", "in", "present", "absent"]},
                    "value": {"description": ("a string for equals and contains, a list of strings for in; "
                                              "omit for present and absent")}},
                    "required": ["column", "op"]}},
                "group_by": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                "code": {"type": "string", "description": "optional; see the rules in your instructions"}},
                "required": ["source"]},
        },
        {
            "name": "answer",
            "description": (
                "Finish the turn. Say what was found. If a filter the user named could "
                "not be applied, or a number is from a different set than the one they "
                "asked about, that goes in caveats — it is not optional."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The reply, in plain prose."},
                    "caveats": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Anything that makes the number less than a direct answer: a "
                            "filter that could not be applied, a cap, a substituted term, "
                            "a set that is not the one asked about. Empty when there are none."
                        ),
                    },
                },
                "required": ["text"],
            },
        },
    ]
    if final:
        return [t for t in schemas if t["name"] == "answer"]
    return schemas if compute else [t for t in schemas if t["name"] != "compute_over_rows"]


def describe_stored_result(bundle: dict) -> dict[str, Any]:
    """What the stored bundle holds, and what it cannot hold.

    ``rows_stored`` against ``total`` is the whole point: in task 118 the user was told
    that a 20-row answer to a 250-row question was normal paging, because nothing in
    the stored bundle said otherwise.

    ``stored_query`` is the query that produced the result (``_stored_query``). A capped
    copy used to be answered by seeding a new query with the UIDs it held, which scoped
    "of those, how many" to 5,000 of 36,622 records and said nothing; a count kept no UIDs
    at all. Either way the set is now rebuilt from this query.

    A complete copy also carries its rows, or a summary of them (``_stored_contents``), so
    "which labs are those from?" about 40 stored records that selected the lab is answered
    from them instead of spending a query on data already on disk.
    """
    graph_result = bundle.get("graph_result") or {}
    api_slim = bundle.get("api_result_slim") or {}
    api_data = api_slim.get("data") if isinstance(api_slim.get("data"), dict) else {}

    rows, payload_total, more_pages, from_plan = _stored_rows_and_extent(bundle)

    if from_plan:
        # A plan's rows are its final step's, with that step's own total and cap
        # (``plan_step_extent``), never the first graph step's or search's.
        total = payload_total
    else:
        # Every total the bundle holds, first found: the graph result's own, the slim API
        # result's, then the one stored beside the rows (memory_payload, then the full API
        # result, which is all a plan-mode search step keeps). A graph result's count is the
        # number of rows returned: once its LIMIT was hit that is not a total.
        total = graph_result.get("total")
        if total is None and not graph_result.get("truncated"):
            total = graph_result.get("count")
        if total is None:
            total = (api_data or {}).get("total")
        if total is None:
            total = payload_total

    uids = _uids_from_rows(rows)
    rows_stored = len(rows)
    stored_query = _stored_query(bundle)

    aggregate = _aggregate_values(rows, len(uids))
    if aggregate:
        numbers = [v for v in aggregate.values() if isinstance(v, (int, float))]
        if len(numbers) == 1:
            # The stored total is the row count of an aggregate, which is always 1.
            total = numbers[0]

    capped = False
    if isinstance(total, int) and rows_stored and total > rows_stored:
        capped = True
    if aggregate:
        # An aggregate stores one row holding the whole answer. It is complete, not capped:
        # total is now the value it computed, and comparing that to a row count of 1 would
        # tell the agent to re-query for a number it already has.
        capped = False
    if (graph_result.get("truncated") and not from_plan) or more_pages:
        # After the aggregate reading, never before it: one UID-less row that hit LIMIT 1
        # looks like an aggregate and is a record cut short. A DRF page with a next page
        # is cut short whatever its count says.
        capped = True
    total_known = total is not None

    described = {
        "bundle_id": bundle.get("id"),
        "user_query": bundle.get("user_query"),
        "mode": bundle.get("mode"),
        "total": total,
        "total_known": total_known,
        "aggregate_values": aggregate,
        "previous_reply": _without_debug_block(bundle.get("terminal_reply")),
        "rows_stored": rows_stored,
        "capped": capped,
        "uid_count": len(uids),
        "uid_sample": uids[:UID_SAMPLE],
        "filters": ((bundle.get("parser_plan") or {}).get("filters") or {}),
        "stored_query": stored_query,
        # The same rule the note below and the orchestrator's seam use: shown to the model
        # so that it is told a set will be rebuilt only when it will be.
        "stored_query_rebuildable": stored_query_rebuildable(stored_query),
        "note": (
            CAPPED_NOTE + CAPPED_NOTE_SCOPING
            if capped and stored_query_rebuildable(stored_query) else
            CAPPED_NOTE_SEEDED
            if capped else
            "The stored copy holds every row of this result."
            if rows_stored and rows_stored == total else
            "This result is an aggregate: aggregate_values holds what it computed, and "
            "total is that value, not a row count. Anything about individual samples "
            "needs a new query."
            if aggregate else
            "This result kept no rows (a count-only query), so anything about "
            "individual samples needs a new query."
            if not rows_stored else
            f"No total was stored for this result, so it is not known whether these "
            f"{rows_stored} stored rows are all of it. An answer from them covers the "
            f"{rows_stored} stored rows, not the whole set: say so."
            if not total_known else
            "The stored copy holds the rows listed above."
        ),
    }
    # The rows themselves, or a summary of them, only when the copy is complete: by the
    # same `capped` the note above uses, so the two cannot disagree about a result.
    described.update(_stored_contents(rows, capped=capped))
    return described


def _stored_query(bundle: dict) -> dict[str, Any] | None:
    """``{cypher, parameters}`` of the graph query that produced ``bundle``, or None.

    The Cypher is the statement the graph agent wrote, as the bundle's ``graph_plan`` keeps
    it, never the scoped text that ran. None for a REST result (no graph plan) and for a
    plan with no Cypher.

    A plan's rows are its final step's (``_step_rows``), and its ``graph_plan`` is its FIRST
    graph step's, which may not be the set the user saw: a later filter, intersection, graph
    step or seeded search changed it. So a plan's stored query is its final rows step's own
    graph plan, and only when that step is a graph step that derives from no other step
    (``_step_inputs``); otherwise None, and a follow-up is scoped to the rows it holds.

    Parameters named with the reserved scope prefix are dropped. A rebuilt query goes back
    through ``tool_neo4j_query``, whose scope prover refuses any caller-supplied parameter
    with that prefix, so carrying the server's own scope parameter forward would refuse
    every rebuilt query for a caller who is not an admin.
    """
    plan = bundle.get("graph_plan")
    final = _final_step_key(bundle)
    if final is not None:
        step_results = bundle["step_results"]
        result = step_results[final]
        output = result.get("output") or {}
        own = output.get("graph_plan") if result.get("tool") == "graph_query" else None
        plan = own if not _step_inputs(step_results, final, _plan_steps(bundle)) else None
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()
    if not isinstance(plan, Mapping):
        return None
    cypher = plan.get("cypher")
    if not isinstance(cypher, str) or not cypher.strip():
        return None
    raw = plan.get("parameters")
    parameters = {
        k: v for k, v in (raw.items() if isinstance(raw, Mapping) else ())
        if not (isinstance(k, str) and k.lower().startswith(RESERVED_PREFIX))
    }
    return {"cypher": cypher, "parameters": parameters}


def stored_query_rebuildable(stored_query: Any) -> bool:
    """Whether a follow-up can start from ``stored_query`` alone.

    Not when it filters on ``$uids``: that is a follow-up's own query, and its bundle keeps
    the UIDs it bound only as a count (``orchestrator._followup_result_bundle``), so a query
    rebuilt from it would have nothing to bind. The note above and the orchestrator's
    ``run_new_query`` seam both read this, so they cannot disagree about a result.
    """
    if not isinstance(stored_query, Mapping):
        return False
    cypher = stored_query.get("cypher")
    return isinstance(cypher, str) and bool(cypher.strip()) and "$uids" not in cypher


def _aggregate_values(rows: list, uid_count: int) -> dict[str, Any] | None:
    """The values an aggregate computed, or None when the rows are sample records.

    ``tool_neo4j_query`` sets ``total = len(records)`` unless the query hit a trailing
    LIMIT, so a count query stores ``count=1, total=1`` and the number it actually
    computed sits inside its single row. A follow-up that read ``total`` therefore
    answered "There is 1 mass-spectrometry data sample in the database" to a question
    about a result whose own reply had said 890.

    One row, no UIDs and scalar values only: a breakdown has rows the agent can read
    for itself, and anything carrying a UID is a record, not an aggregate.
    """
    if uid_count or len(rows) != 1 or not isinstance(rows[0], dict) or not rows[0]:
        return None
    values = {k: v for k, v in rows[0].items() if isinstance(v, (int, float, str))}
    return values if len(values) == len(rows[0]) else None


def _stored_rows(bundle: dict) -> list:
    """Rows from wherever this bundle put them, without loading a payload twice."""
    return _stored_rows_and_extent(bundle)[0]


def _rows_in(data: Any) -> list | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("rows", "samples", "results", "nodes"):
            if isinstance(data.get(key), list):
                return data[key]
    return None


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _payload_extent(data: Any) -> tuple[int | None, bool]:
    """``(total, a further page follows)`` as a stored result body states them.

    ``{"rows", "total"}`` is a search, and a plan-mode step's copy of one; ``truncated`` true
    says it is cut short (a plan filter over a capped step, whose total is not known). A DRF
    page is ``{count, next, previous, results}``: ``count`` is its total, and a ``next`` that
    is not null means a page follows, with or without a count.
    """
    if not isinstance(data, Mapping):
        return None, False
    total = data["total"] if _is_count(data.get("total")) else None
    page = isinstance(data.get("results"), list)
    if total is None and page and _is_count(data.get("count")):
        total = data["count"]
    return total, (page and data.get("next") is not None) or data.get("truncated") is True


def _stored_rows_and_extent(bundle: dict) -> tuple[list, int | None, bool, bool]:
    """``(rows, total stored beside them, a further page follows or they were cut short, from a plan step)``.

    A plan bundle's rows are its final step's (``_step_rows``), read first: its graph result is
    its first graph step's, and its memory payload the builder's pick, and either can be an
    earlier set than the one the plan ended with.
    A graph result's rows come with its own total, which ``describe_stored_result`` reads.
    Any other result's rows and total come from memory_payload, then the full API result,
    the first total found winning: a plan-mode filter step keeps its own rows and total in
    memory_payload, and the larger total of the search it filtered, in the full API result,
    is not theirs. A memory payload that was just read back from the full API result is not
    loaded a second time.
    """
    plan_rows, plan_total, plan_capped = _step_rows(bundle)
    if plan_rows is not None:
        return plan_rows, plan_total, plan_capped, True

    graph_result = bundle.get("graph_result") or {}
    if isinstance(graph_result.get("data"), list):
        return graph_result["data"], None, False, False

    def sources():
        yield load_memory_payload(bundle)
        kept = bundle.get("memory_payload")
        if not (isinstance(kept, dict) and kept and "data" not in kept):
            yield load_api_result_full(bundle)

    rows: list | None = None
    total: int | None = None
    more = False
    for candidate in sources():
        if not isinstance(candidate, dict):
            continue
        data = candidate.get("data")
        if rows is None:
            rows = _rows_in(data)
        stated, follows = _payload_extent(data)
        if total is None:
            total = stated
        more = more or follows
        if rows is not None and total is not None:
            break
    return rows or [], total, more, False


def _step_rows(bundle: dict) -> tuple[list | None, int | None, bool]:
    """``(rows, total, capped)`` of the step that produced a plan bundle's final result: the LAST successful step
    in ``step_results`` whose output holds rows, so "those" after a search and a filter is the filtered set. Its
    total and cap are ``plan_step_extent``'s. ``(None, None, False)`` when no step holds rows.
    """
    last = _final_step_key(bundle)
    if last is None:
        return None, None, False
    step_results = bundle["step_results"]
    total, capped = plan_step_extent(step_results, last, plan_steps=_plan_steps(bundle))
    return _step_rows_of(step_results[last]), total, capped


def _final_step_key(bundle: dict) -> Any:
    """The ``step_results`` key of the LAST successful plan step whose output holds rows, or None."""
    step_results = bundle.get("step_results")
    if not isinstance(step_results, Mapping):
        return None
    last = None
    for key, result in step_results.items():
        if _step_rows_of(result) is not None:
            last = key
    return last


def _step_rows_of(result: Any) -> list | None:
    """A successful plan step's rows, or None."""
    if not isinstance(result, Mapping) or not result.get("ok"):
        return None
    output = result.get("output")
    return output["data"] if isinstance(output, Mapping) and isinstance(output.get("data"), list) else None


def _plan_steps(bundle: dict) -> list:
    """The plan's steps as the bundle keeps them (``plan``, the planner output), or []."""
    plan = bundle.get("plan")
    steps = plan.get("steps") if isinstance(plan, Mapping) else None
    return list(steps) if isinstance(steps, list) else []


def _field(step: Any, name: str) -> Any:
    return step.get(name) if isinstance(step, Mapping) else getattr(step, name, None)


def _find_step(step_results: Mapping, key: Any) -> tuple[Any, Any]:
    """``(key as stored, result)`` for ``key``, matched as text: a session round trip makes an int key a string."""
    for stored, result in step_results.items():
        if str(stored) == str(key):
            return stored, result
    return None, None


def _step_inputs(step_results: Mapping, key: Any, plan_steps: list) -> list:
    """The earlier steps a plan step's rows derive from: a filter's ``source_step_id``; for the intersection, the
    steps the plan marks ``intersect``, or every other step when the plan's steps are not known; and for any step,
    the ``depends_on`` and ``input_mapping`` sources the plan records for it (a search seeded with an earlier step's
    UIDs)."""
    result = step_results.get(key)
    output = result.get("output") if isinstance(result, Mapping) else None
    output = output if isinstance(output, Mapping) else {}
    inputs: list = []
    if result.get("tool") == "coding_filter" or "source_step_id" in output:
        if output.get("source_step_id") is not None:
            inputs.append(output["source_step_id"])
    if result.get("tool") == "intersection" or str(key) == "intersection":
        marked = [_field(s, "step_id") for s in plan_steps if _field(s, "combine_mode") == "intersect"]
        inputs.extend(marked if plan_steps else [k for k in step_results if str(k) != str(key)])
    step = next((s for s in plan_steps if str(_field(s, "step_id")) == str(key)), None)
    if step is not None:
        if _field(step, "depends_on") is not None:
            inputs.append(_field(step, "depends_on"))
        mapping = _field(step, "input_mapping")
        for ref in (mapping.values() if isinstance(mapping, Mapping) else ()):
            if _field(ref, "from_step") is not None:
                inputs.append(_field(ref, "from_step"))
    return [i for i in inputs if str(i) != str(key)]


def _own_extent(output: Mapping) -> tuple[int | None, bool]:
    """``(total, capped)`` as one step's output states them. A graph step's ``count`` is only the number of rows
    returned, so its ``total`` is its own ``total`` (``orchestrator._plan_graph_result``), and a step that hit its
    LIMIT is ``truncated``. A step without a ``total`` (a search, a filter) has its ``count`` as its total unless it
    was cut short. Capped means truncated, or a total larger than the rows it holds."""
    rows = output.get("data") if isinstance(output.get("data"), list) else []
    truncated = bool(output.get("truncated"))
    total = output["total"] if _is_count(output.get("total")) else None
    if total is None and not truncated and _is_count(output.get("count")):
        total = output["count"]
    return total, truncated or (total is not None and total > len(rows))


def plan_step_extent(step_results: Mapping, key: Any, *, plan_steps: list | None = None) -> tuple[int | None, bool]:
    """``(total, capped)`` of plan step ``key``'s rows.

    A step is capped by its own output (``_own_extent``), or by any earlier successful capped step it derives from
    (``_step_inputs``, followed through every step in between): a filter over 1,000 of 36,622 rows holds a filtered
    part of a part, however whole its own count looks. An inherited cap reads as capped with an unknown total.
    """
    plan_steps = list(plan_steps or [])
    stored, result = _find_step(step_results, key)
    output = result.get("output") if isinstance(result, Mapping) else None
    if not isinstance(output, Mapping):
        return None, False
    total, capped = _own_extent(output)
    if capped:
        return total, True
    seen = {str(stored)}
    pending = list(_step_inputs(step_results, stored, plan_steps))
    while pending:
        source, source_result = _find_step(step_results, pending.pop())
        if source is None or str(source) in seen:
            continue
        seen.add(str(source))
        if _step_rows_of(source_result) is None:
            continue
        if _own_extent(source_result["output"])[1]:
            return None, True
        pending.extend(_step_inputs(step_results, source, plan_steps))
    return total, False


def _uids_from_rows(rows: list) -> list[str]:
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("uid", "UID", "uuid", "UUID", "id", "name"):
            value = row.get(key)
            if isinstance(value, str) and value:
                out.append(value)
                break
    return out


def _all_uids(bundle: dict) -> list[str]:
    return _uids_from_rows(_stored_rows(bundle))


def run_followup(
    config: ChatConfig,
    *,
    user_text: str,
    bundle: dict,
    run_query: Any,
    compute: Any = None,
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Drive the loop and return ``{reply, caveats, queries, computes, tool_calls}``.

    ``run_query(question, seed_uids, stored_query)`` is injected rather than imported so
    this module does not depend on the orchestrator (which imports it), and so a test can
    drive the loop without a graph. A scoped query (``seed_uids`` true) is handed every
    stored UID and the stored query, and the seam decides which scopes it; a fresh
    question is handed neither. ``scoped`` says which of the two it is, explicitly: with
    no UIDs and no stored query the two look the same, and only a scoped one must say
    that it could not be scoped.

    ``compute(source, where, group_by, code)``, when given, runs ``compute_over_rows`` and
    offers it to the model; each call is kept in ``computes`` with its result.

    When the model and its fallback both fail (``LLMFatalError``) after the loop has run a
    query or a computation, it returns what it has with ``reply`` None and
    ``model_unavailable`` set; before that, the fatal propagates.
    """
    client, model_name, thinking_budget = config.get_agent_model(FOLLOWUP_AGENT_KEY)
    if not callable(getattr(client, "chat_with_tools", None)):
        # No tool surface on this profile. The caller answers with
        # FOLLOWUP_UNAVAILABLE_REPLY; nothing answers from the stored snapshot.
        return {"reply": None, "caveats": [], "queries": [], "computes": [], "tool_calls": [],
                "unsupported": True}

    system_prompt = config._load_prompt("followup_agent.txt")
    messages: list[dict] = [{
        "role": "user",
        "content": (
            f"The user's follow-up question: {user_text}\n\n"
            f"It follows this earlier question: {bundle.get('user_query') or '[unknown]'}\n"
            f"The stored result for it has id {bundle.get('id')}."
        ),
    }]

    queries: list[dict] = []
    computes: list[dict] = []
    tool_calls: list[str] = []
    read_already = False

    # MAX_ITER working iterations, then one pass that can only answer -- taken only when
    # something was queried or computed. A loop that just read the bundle has nothing to
    # report, and the caller then answers with FOLLOWUP_UNAVAILABLE_REPLY.
    for iteration in range(MAX_ITER + 1):
        terminal = iteration == MAX_ITER
        if terminal:
            if not (queries or computes):
                break
            messages.append({"role": "user", "content": (
                "This is your final turn and only `answer` is available. Answer now from the "
                "results you already have, and put anything you could not establish in "
                "`caveats`. Nothing else can run."
            )})
        try:
            resp = call_tools(
                config,
                messages=messages,
                tools=build_followup_tool_schemas(final=terminal, compute=compute is not None),
                system=system_prompt,
                model_name=model_name,
                client=client,
                agent_label=FOLLOWUP_AGENT_KEY,
                thinking_budget=thinking_budget,
            )
        except LLMFatalError as fatal:
            # The model and its fallback both failed. A loop that already ran a query or
            # a computation keeps what it found: the caller answers from it
            # (resolve_followup_outcome), as for a loop that ran out of turns, rather than
            # the turn ending and those results being dropped. One that ran nothing lets
            # the fatal through, and the user is told the models were unavailable.
            if not (queries or computes):
                raise
            print(f"[DEBUG][FOLLOWUP] model failure after {len(queries)} query(ies), "
                  f"{len(computes)} computation(s); answering from them: {fatal}")
            return {"reply": None, "caveats": [], "queries": queries, "computes": computes,
                    "tool_calls": tool_calls, "model_unavailable": bool(getattr(fatal, "unavailable", False))}
        content = resp.get("content") or []
        tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]

        if not tool_uses:
            # Prose without finishing through `answer`. Take the text rather than
            # spending another iteration on a model that has already answered.
            text = "\n".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            ).strip()
            return {"reply": text or None, "caveats": [], "queries": queries,
                    "computes": computes, "tool_calls": tool_calls}

        messages.append({"role": "assistant", "content": content})
        results: list[dict] = []
        for block in tool_uses:
            name = block.get("name")
            tool_input = block.get("input") or {}
            tool_calls.append(name)

            if name == "answer":
                return {
                    "reply": tool_input.get("text") or None,
                    "caveats": list(tool_input.get("caveats") or []),
                    "queries": queries,
                    "computes": computes,
                    "tool_calls": tool_calls,
                }

            if terminal:
                # It called something that was not offered. Run nothing: the point of this
                # pass is that the answer comes from what is already in the conversation.
                payload = {"ok": False, "error": (
                    f"`{name}` was not available on this turn and did not run. Only `answer` "
                    "was, and this was the last turn."
                )}
            elif name == "read_stored_result":
                if read_already:
                    # It cannot have changed, and it is the largest payload in the loop: three
                    # of six iterations went on re-reading it on 2026-09-22.
                    payload = {"ok": False, "already_read": True, "note": (
                        "You have already read the stored result this turn and it cannot have "
                        "changed. Run a query or call `answer`."
                    )}
                else:
                    read_already = True
                    payload = describe_stored_result(bundle)
            elif name == "run_new_query":
                question = (tool_input.get("question") or "").strip() or user_text
                seed = bool(tool_input.get("seed_uids", True))
                try:
                    payload = run_query(question=question, seed_uids=_all_uids(bundle) if seed else [],
                                        stored_query=_stored_query(bundle) if seed else None,
                                        scoped=seed)
                except Exception as exc:
                    payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                queries.append({"question": question, "seeded": seed, "result": payload})
            elif name == "compute_over_rows" and compute is not None:
                call = {"source": tool_input.get("source") or "stored",
                        "where": tool_input.get("where") or None,
                        "group_by": tool_input.get("group_by") or None,
                        "code": tool_input.get("code") or None}
                try:
                    payload = compute(**call)
                except Exception as exc:
                    payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                computes.append({**call, "result": payload})
            else:
                payload = {"ok": False, "error": f"unknown tool {name!r}"}

            results.append({
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": json.dumps(payload, default=str),
            })
        messages.append({"role": "user", "content": results})

    return {"reply": None, "caveats": [], "queries": queries, "computes": computes,
            "tool_calls": tool_calls, "exhausted": True}
