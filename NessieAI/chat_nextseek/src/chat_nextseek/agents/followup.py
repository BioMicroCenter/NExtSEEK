"""The follow-up agent: a tool loop where the memory branch used to be a dead end.

``mode == "ask_about_last_results"`` was terminal. It picked one stored bundle, handed
it to the memory agent, and returned; there was no path from that branch back to the
graph. So a follow-up that needed data the stored result could not contain was answered
from the stored result anyway. The production review calls this the second largest
cause of bad answers, 10 of 53:

* wesselr 439/440 — after 129 IMPACT patients: "do any of those also have RNA
  sequencing data?" got "it is not possible to determine", and "what other types of
  data are available?" got "No other data types are available". Twenty minutes later
  the same user asked it as a fresh question and got nine downstream data types. 440
  told the user something false.
* mchao 117/118/119 — the stored result held 20 rows of 250, so the follow-up answered
  from 20, explained the 20 as paging, and exported 20.
* wesselr 427/428 — "which labs are those mouse samples from?" after a count-only
  query, which kept no rows at all.

Nothing was forgotten in any of those. The UIDs were on disk. What was missing was the
ability to decide to run another query with them, so this module gives the model three
tools and lets it choose:

* ``read_stored_result`` — what the stored bundle holds, and what it does NOT: the row
  count against the real total, whether it was capped, how many UIDs are available.
* ``run_new_query`` — re-run against the graph seeded with those UIDs.
* ``answer`` — finish, with any caveats as a required field rather than an instruction.

``read_stored_result`` returns counts and a handful of examples, never rows: the stored
rows are the previous turn's, already answered. ``run_new_query`` returns the head of its
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
from typing import Any

from ..artifacts import load_api_result_full, load_memory_payload
from ..config import ChatConfig
from ..tool_loop import call_tools

FOLLOWUP_AGENT_KEY = "followup"

#: Generate -> look -> decide, bounded. Each iteration is a model call, and a follow-up
#: that cannot finish in three has misunderstood the question rather than run short.
MAX_ITER = 6

#: How many example identifiers a tool result carries. Enough for the reply to quote
#: some verbatim, small enough that re-sending it every iteration costs nothing.
UID_SAMPLE = 5


#: How many of a new query's rows the model is shown, and the most characters they may
#: take. A breakdown by type or lab is a few dozen short rows and fits whole; a list of
#: sample records shows its head. Re-sent on every later iteration, so kept small.
FOLLOWUP_ROWS_MAX = 50
FOLLOWUP_ROWS_CHARS = 6_000


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


#: A NExtSEEK reply carries a fenced debug block. On turn 1147 `read_stored_result`
#: returned ~1,000 tokens, mostly that block, and it is re-sent on every later iteration.
_DEBUG_MARKER = "**Debug info**"


def _without_debug_block(reply: Any) -> str | None:
    """The user-facing half of an earlier reply."""
    if not isinstance(reply, str) or not reply:
        return reply if reply is None else ""
    at = reply.find(_DEBUG_MARKER)
    return (reply[:at] if at > 0 else reply).strip()


def _reply_from_queries(queries: list[dict]) -> str | None:
    """What the loop established, when it ran out of turns before saying it.

    Worse than an answer the model composed, and far better than the stored-result path,
    which cannot see what these queries returned and on turn 1147 reported its absence.
    """
    ran = [q for q in queries or [] if isinstance(q, dict)]
    if not ran:
        return None
    found = [q for q in ran if (q.get("result") or {}).get("ok") and (q.get("result") or {}).get("count")]
    if not found:
        return ("I could not finish checking this. The follow-up query I ran did not come back with "
                "anything I can stand behind, so ask it as a fresh question and I will run it properly.")
    last = found[-1]
    result = last["result"]
    examples = [str(e) for e in (result.get("examples") or [])][:5]
    parts = [f"{result['count']:,} records match, from a follow-up query over the previous result."
             if isinstance(result.get("count"), int) else "The follow-up query found records."]
    if examples:
        parts.append("Examples: " + ", ".join(examples) + ".")
    parts.append("I ran out of steps before I could finish, so treat this as partial: "
                 "ask it again and I will answer it properly.")
    return " ".join(parts)


def resolve_followup_outcome(outcome: dict | None) -> tuple[str | None, bool]:
    """``(reply, the stored-result path may answer instead)``.

    The whole of turn 1147's defect is that the caller had only ``if reply:`` to tell
    three completed graph queries from a profile with no tool surface, so it answered a
    lineage question from a five-column bundle and asserted the absence of what the
    queries had found. The rule: the stored path answers only when nothing queried the
    graph on this turn.
    """
    if not outcome or outcome.get("unsupported"):
        return None, True
    reply = outcome.get("reply")
    if reply:
        caveats = [str(c) for c in (outcome.get("caveats") or []) if str(c).strip()]
        if caveats:
            reply = reply + "\n\n" + "\n".join(f"- {c}" for c in caveats)
        return reply, False
    if outcome.get("queries"):
        return _reply_from_queries(outcome["queries"]), False
    return None, True


def build_followup_tool_schemas(*, final: bool = False) -> list[dict]:
    """The three tools, in the order a follow-up naturally uses them.

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
                "UIDs are available, and a few example UIDs. It does NOT return the "
                "rows. If rows_stored is less than total, the stored copy cannot answer "
                "a question about the whole set and you must run a new query."
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
                "Run a NEW query against the graph, seeded with the previous result's "
                "UIDs, and get back its count and its rows (the first "
                f"{FOLLOWUP_ROWS_MAX} at most: `rows`, with `rows_shown` of "
                "`rows_returned`). Use this whenever the question needs data the stored "
                "result cannot contain: a different data type, a property that was not "
                "selected, or anything about rows beyond the stored ones. Answer from "
                "the rows: when they are a breakdown, name each value and its count."
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
                            "True to scope the query to the previous result's UIDs. "
                            "This is what makes 'of those, how many...' mean the same "
                            "set the user is asking about."
                        ),
                    },
                },
                "required": ["question"],
            },
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
    return [t for t in schemas if t["name"] == "answer"] if final else schemas


def describe_stored_result(bundle: dict) -> dict[str, Any]:
    """What the stored bundle holds, and what it cannot hold.

    ``rows_stored`` against ``total`` is the whole point: mchao 118 was told that a
    20-row answer to a 250-row question was normal paging, because nothing in the
    stored bundle said otherwise.
    """
    graph_result = bundle.get("graph_result") or {}
    api_slim = bundle.get("api_result_slim") or {}
    api_data = api_slim.get("data") if isinstance(api_slim.get("data"), dict) else {}

    total = (
        graph_result.get("total")
        if graph_result.get("total") is not None
        else graph_result.get("count")
    )
    if total is None:
        total = (api_data or {}).get("total")

    rows = _stored_rows(bundle)
    uids = _uids_from_rows(rows)
    rows_stored = len(rows)

    aggregate = _aggregate_values(rows, len(uids))
    if aggregate:
        numbers = [v for v in aggregate.values() if isinstance(v, (int, float))]
        if len(numbers) == 1:
            # The stored total is the row count of an aggregate, which is always 1.
            total = numbers[0]

    capped = False
    if isinstance(total, int) and rows_stored and total > rows_stored:
        capped = True
    if graph_result.get("truncated"):
        capped = True
    if aggregate:
        # An aggregate stores one row holding the whole answer. It is complete, not capped:
        # total is now the value it computed, and comparing that to a row count of 1 would
        # tell the agent to re-query for a number it already has.
        capped = False

    return {
        "bundle_id": bundle.get("id"),
        "user_query": bundle.get("user_query"),
        "mode": bundle.get("mode"),
        "total": total,
        "aggregate_values": aggregate,
        "previous_reply": _without_debug_block(bundle.get("terminal_reply")),
        "rows_stored": rows_stored,
        "capped": capped,
        "uid_count": len(uids),
        "uid_sample": uids[:UID_SAMPLE],
        "filters": ((bundle.get("parser_plan") or {}).get("filters") or {}),
        "note": (
            "The stored copy holds fewer rows than the total, so it cannot answer a "
            "question about the whole set. Run a new query seeded with the UIDs."
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
            "The stored copy holds the rows listed above."
        ),
    }


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
    graph_result = bundle.get("graph_result") or {}
    if isinstance(graph_result.get("data"), list):
        return graph_result["data"]

    memory_payload = load_memory_payload(bundle) or {}
    for candidate in (memory_payload, load_api_result_full(bundle)):
        if not isinstance(candidate, dict):
            continue
        data = candidate.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("rows", "samples", "results", "nodes"):
                if isinstance(data.get(key), list):
                    return data[key]
    return []


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
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Drive the loop and return ``{reply, caveats, queries, tool_calls}``.

    ``run_query(question, seed_uids)`` is injected rather than imported so this module
    does not depend on the orchestrator (which imports it), and so a test can drive the
    loop without a graph.
    """
    client, model_name, thinking_budget = config.get_agent_model(FOLLOWUP_AGENT_KEY)
    if not callable(getattr(client, "chat_with_tools", None)):
        # No tool surface on this profile. The caller falls back to the old
        # read-the-stored-result path, which is worse but is what shipped before.
        return {"reply": None, "caveats": [], "queries": [], "tool_calls": [],
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
    tool_calls: list[str] = []
    read_already = False

    # MAX_ITER working iterations, then one pass that can only answer -- taken only when
    # something was queried, because a loop that just read the bundle has nothing to
    # report and the stored-result path is then the only thing that can speak.
    for iteration in range(MAX_ITER + 1):
        terminal = iteration == MAX_ITER
        if terminal:
            if not queries:
                break
            messages.append({"role": "user", "content": (
                "This is your final turn and only `answer` is available. Answer now from the "
                "results you already have, and put anything you could not establish in "
                "`caveats`. Nothing else can run."
            )})
        resp = call_tools(
            config,
            messages=messages,
            tools=build_followup_tool_schemas(final=terminal),
            system=system_prompt,
            model_name=model_name,
            client=client,
            agent_label=FOLLOWUP_AGENT_KEY,
            thinking_budget=thinking_budget,
        )
        content = resp.get("content") or []
        tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]

        if not tool_uses:
            # Prose without finishing through `answer`. Take the text rather than
            # spending another iteration on a model that has already answered.
            text = "\n".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            ).strip()
            return {"reply": text or None, "caveats": [], "queries": queries,
                    "tool_calls": tool_calls}

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
                    payload = run_query(question=question, seed_uids=_all_uids(bundle) if seed else [])
                except Exception as exc:
                    payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                queries.append({"question": question, "seeded": seed, "result": payload})
            else:
                payload = {"ok": False, "error": f"unknown tool {name!r}"}

            results.append({
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": json.dumps(payload, default=str),
            })
        messages.append({"role": "user", "content": results})

    return {"reply": None, "caveats": [], "queries": queries, "tool_calls": tool_calls,
            "exhausted": True}
