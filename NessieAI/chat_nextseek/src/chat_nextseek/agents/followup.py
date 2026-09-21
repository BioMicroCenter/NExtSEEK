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

Every tool returns counts and a handful of examples, never rows. A tool loop re-sends
its whole conversation on each iteration, so a tool that returned a result set would be
paid for once per remaining iteration; the same reasoning that keeps the full payload
out of ``results_history`` (see ``artifacts.py``) keeps it out of the conversation.
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


def build_followup_tool_schemas() -> list[dict]:
    """The three tools, in the order a follow-up naturally uses them."""
    return [
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
                "UIDs, and get back its count plus a few example UIDs. Use this whenever "
                "the question needs data the stored result cannot contain: a different "
                "data type, a property that was not selected, or anything about rows "
                "beyond the stored ones. Returns counts, never rows."
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
        "previous_reply": bundle.get("terminal_reply"),
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

    for _ in range(MAX_ITER):
        resp = call_tools(
            config,
            messages=messages,
            tools=build_followup_tool_schemas(),
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

            if name == "read_stored_result":
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
