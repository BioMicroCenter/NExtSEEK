"""The ``aggregate`` op: counts and breakdowns inside the caller's projects, in one call.

A question such as "how many, broken down by what" gets one to four parts, written by the CC agent in plain
language (``parts``; without them the question is the one part). The op resolves the vocabulary once, over the
question and every part, so each part reads the same terms. Then each part runs the graph op's own chain
(``granular.run_graph_question``) on a small thread pool: the parser, the graph agent with ``AGGREGATE_BRIEF``,
the Neo4j tool, at most one retry, and the graph_search fallback on a scope refusal. What comes back is a small
table per part (``groups``, ``sum_of_group_counts``, ``groups_may_overlap``, ``null_group``), never sample records:
the agent charts or clusters it.

A breakdown's ``sum_of_group_counts`` adds its groups' counts, so a sample that falls in several groups (a project,
an assay or a study breakdown, a list value) counts once in each: it is the number of samples only when no sample can
sit in two groups, which the rows cannot show. ``groups_may_overlap`` is true for every breakdown of two groups or
more; a total is asked as its own part.

Scope. The handler takes the request's config as the view built it and hands that same config to every call.
It builds no config and takes no Cypher and no project list from the caller: the request model forbids those
fields, and parts are plain language. Every statement, a retry's too, runs through the scoped Neo4j tool, which
runs a superuser's statement as written and holds anyone else's to their projects or refuses it; a refusal is
answered through graph_search, which applies the caller's projects on the server and returns a total, so a
refused breakdown comes back as that total with a note saying the breakdown could not be computed.

Time. The sidecar gives the op 60 s. The op answers at ``OP_DEADLINE_S`` with whatever has finished and names the
other parts ``timed_out``; work already started finishes in the background, as the graph op's does. A retry or a
fallback starts only while at least ``MIN_REMAINING_S`` remain.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable

from NessieAI.ns.granular import GRAPH_SCOPE_FALLBACK_NOTE, OpValidationError, run_graph_question

#: The op's own answer time, inside the sidecar's 60 s request timeout (ns-sidecar/app/ns_client.py).
OP_DEADLINE_S = 50.0
#: A retry or a graph_search fallback starts only while at least this much of the deadline is left.
MIN_REMAINING_S = 15.0
MAX_PARTS = 4
MAX_PART_CHARS = 2000
#: Groups (or rows) a part returns. The op caps a statement at one more, so the tool's probe counts the rest.
GROUP_CAP = 1000
ROW_CAP = GROUP_CAP + 1
#: Refusal codes a rewrite can cure: one statement written without the subquery or the UNION.
SHAPE_CODES = frozenset({"union", "call_subquery", "collect_subquery"})
#: The prover skips a refused subquery, so the names bound inside it then read as unbound and it adds ``syntax``
#: to the refusal. With a shape code beside it, that ``syntax`` is the same finding, and the rewrite cures both.
SHAPE_FOLLOW_ON_CODES = frozenset({"syntax"})
_POLL_S = 0.2
_monotonic = time.monotonic

#: Handed to every graph agent call of this op (its ``refine_context``).
AGGREGATE_BRIEF = (
    "AGGREGATE QUERY (the nextseek-aggregate op): return aggregate rows only, never sample records. "
    "For a breakdown, return one row per group: the group values first, then count(DISTINCT s) AS n, "
    "ordered by n descending. Keep stored values exactly as written: do not lower-case, trim or merge "
    "spellings. Do not filter out samples that lack the grouping attribute: they are their own row, "
    "with a null group value. For a single number, return one row holding the count. Write one statement "
    "with no CALL, UNION or COLLECT subquery (EXISTS { } and COUNT { } are fine). Answer only the question "
    "given."
)

#: The name AGGREGATE_BRIEF gives a breakdown's count column, which comes last.
BREAKDOWN_COUNT_COLUMN = "n"

_LIMIT_WORD_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_LIMIT_OPERAND_RE = re.compile(r"\s+(?:\d+|\$\w+)")


def _clock() -> float:
    return _monotonic()


def parse_parts(raw: Any, question: str) -> list[str]:
    """The parts to answer: ``[question]`` when none were given, else 1 to ``MAX_PARTS`` non-empty strings."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return [question]
    if not isinstance(raw, str):
        raise OpValidationError("parts must be a JSON array of strings, sent as text")
    try:
        parts = json.loads(raw)
    except ValueError as exc:
        raise OpValidationError(f"parts is not valid JSON: {exc}") from exc
    if not isinstance(parts, list) or not all(isinstance(part, str) for part in parts):
        raise OpValidationError("parts must be a JSON array of strings")
    parts = [part.strip() for part in parts]
    if not 1 <= len(parts) <= MAX_PARTS:
        raise OpValidationError(f"parts must hold 1 to {MAX_PARTS} sub-questions, not {len(parts)}")
    if not all(parts):
        raise OpValidationError("every part must be a non-empty sub-question")
    if any(len(part) > MAX_PART_CHARS for part in parts):
        raise OpValidationError(f"a part may be at most {MAX_PART_CHARS} characters")
    return parts


def _has_own_limit(cypher: str, masked: str) -> bool:
    """Does the statement end in a LIMIT of its own, whatever comments follow it?

    Read on ``masked`` (``mask_cypher`` blanks strings, backticked names, parameters and comments), so a LIMIT inside
    a comment or a string is not one, and a comment after the real one does not hide it. The operand is read on the
    original, where a parameter is still visible.
    """
    words = list(_LIMIT_WORD_RE.finditer(masked))
    if not words or masked[words[-1].start() - 1:words[-1].start()] == ".":
        return False
    operand = _LIMIT_OPERAND_RE.match(cypher, words[-1].end())
    return operand is not None and not masked[operand.end():].replace(";", " ").strip()


def cap_rows(cypher: str, parameters: dict) -> str:
    """The statement with ``LIMIT 1001`` appended when it has no trailing LIMIT of its own.

    Appended before the tool runs it, so the prover checks it too. A runaway grouping (by UID, say) cannot pull a
    million rows into the worker, and when the cap is hit the tool's probe reports the true number of groups. It goes
    on a line of its own, so a trailing line comment cannot swallow it, and a trailing ``;`` goes, even with a comment
    after it.
    """
    from chat_nextseek.cypher_text import mask_cypher

    masked = mask_cypher(cypher)
    if _has_own_limit(cypher, masked):
        return cypher
    code_end = len(masked.rstrip(" \t\r\n;"))
    body = cypher[:code_end] + "".join(ch for ch, m in zip(cypher[code_end:], masked[code_end:]) if m != ";")
    return f"{body.rstrip()}\nLIMIT {ROW_CAP}"


def shape_repair_context(reasons: list[str]) -> str:
    """The one rewrite a statement refused only for its shape gets, with the prover's reasons."""
    listed = "; ".join(reasons) or "a subquery or UNION"
    return (
        "Your previous Cypher query was not run: it could not be confirmed to stay within the user's projects, "
        f"because of its shape ({listed}). Rewrite it as ONE statement with no CALL, UNION or COLLECT subquery "
        "(EXISTS { } and COUNT { } are fine) that answers the same question with the same filters."
    )


def _retry_for(result: dict, cypher: str, deadline: float) -> tuple[str, str] | None:
    """At most one more statement: after zero rows, or after a refusal a rewrite can cure; never late."""
    from chat_nextseek.graph_retry import zero_row_retry_context
    from chat_nextseek.helpers.tools.neo4j import is_scope_refusal, matched_nothing

    scope = result.get("scope") if isinstance(result.get("scope"), dict) else {}
    codes = set(scope.get("codes") or ())
    if matched_nothing(result):
        reason = "zero_rows"
    elif is_scope_refusal(result) and codes & SHAPE_CODES and codes <= SHAPE_CODES | SHAPE_FOLLOW_ON_CODES:
        reason = "shape_repair"
    else:
        return None
    if deadline - _clock() < MIN_REMAINING_S:
        return None
    if reason == "zero_rows":
        return reason, zero_row_retry_context(cypher)
    return reason, shape_repair_context(list(scope.get("reasons") or ()))


def _brief(question: str, multi: bool, uid_note: str | None) -> str:
    blocks = [AGGREGATE_BRIEF]
    if multi:
        blocks.append(f"This question is one part of a larger one, given for context only: {question}")
    if uid_note:
        blocks.append(uid_note)
    return "\n\n".join(blocks)


def _blank_part(k: int, text: str, status: str) -> dict:
    return {"part": k, "question": text, "status": status, "kind": None, "columns": [], "groups": [],
            "group_count": None, "sum_of_group_counts": None, "groups_may_overlap": None, "null_group": None,
            "truncated": False, "cypher": None, "scope": None, "attempts": [], "fallback": None}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _one_numeric_group(row: dict, columns: list[str]) -> bool:
    """A one-row breakdown whose group value is a number: group columns, then the brief's count column ``n``."""
    return (len(columns) >= 2 and columns[-1].lower() == BREAKDOWN_COUNT_COLUMN
            and _is_count(row[columns[-1]]))


def _classify(rows: list) -> tuple[str, list[str]]:
    """``count`` (one all-numeric row), ``breakdown`` (group columns then a trailing count) or ``rows``.

    One all-numeric row is a count, several numbers of one (roots and leaves, say), unless it ends in the brief's
    count column ``n`` after at least one other: then it is a breakdown with one numeric group (every mouse aged 5).
    """
    if not rows:
        return "breakdown", []
    if not all(isinstance(row, dict) for row in rows):
        return "rows", []
    columns = list(rows[0])
    if not columns or any(list(row) != columns for row in rows):
        return "rows", columns
    if (len(rows) == 1 and all(_is_number(value) for value in rows[0].values())
            and not _one_numeric_group(rows[0], columns)):
        return "count", columns
    if len(columns) >= 2 and all(_is_count(row[columns[-1]]) for row in rows):
        return "breakdown", columns
    return "rows", columns


def _scope_summary(result: dict) -> dict | None:
    scope = result.get("scope")
    if not isinstance(scope, dict):
        return None
    out = {"decision": scope.get("decision")}
    if "project_ids" in scope:
        out["project_ids"] = list(scope.get("project_ids") or ())
    if scope.get("codes"):
        out["codes"] = list(scope["codes"])
    return out


def _fallback_summary(fallback: dict) -> dict:
    """The graph_search answer without its page of sample rows: a total is all a breakdown can use."""
    out = {key: fallback.get(key) for key in ("ok", "ran", "endpoint", "note", "codes", "reasons", "error",
                                               "parser_plan", "status_code") if key in fallback}
    data = fallback.get("data")
    if isinstance(data, dict):
        out.update({key: data[key] for key in ("total", "rows_missing", "sample_types") if key in data})
    return out


def _shape_part(k: int, text: str, answer, label: str) -> tuple[dict, list[str]]:
    """One part's table and the notes the agent must relay."""
    from chat_nextseek.graph_retry import RETRY_CHANGED_ANSWER_NOTE
    from chat_nextseek.helpers.tools.neo4j import matched_nothing

    result = answer.result if isinstance(answer.result, dict) else {}
    notes: list[str] = []
    part = _blank_part(k, text, "ok")
    part.update(cypher=result.get("submitted_cypher") or answer.cypher, scope=_scope_summary(result),
                attempts=answer.attempts)
    if answer.fallback is not None:
        summary = _fallback_summary(answer.fallback)
        part["fallback"] = summary
        if answer.fallback.get("ok") and isinstance(summary.get("total"), int):
            total = summary["total"]
            # graph_search counts each matching sample once: one group, which cannot overlap.
            part.update(status="fallback", kind="count", sum_of_group_counts=total, groups_may_overlap=False)
            notes.append(
                f"{label}{GRAPH_SCOPE_FALLBACK_NOTE} That search counted {total:,} matching samples inside the "
                "user's projects, but the count could not be broken down: report the total and say the "
                "breakdown could not be computed."
            )
        else:
            error = summary.get("error") or "the project-scoped sample search gave no total"
            part.update(status="refused", error=error)
            notes.append(
                f"{label}this part could not be answered: its graph query could not be confirmed to stay within "
                f"the user's projects, and the project-scoped sample search did not answer ({error}). Say so; "
                "never estimate it."
            )
        return part, notes
    if not result.get("ok"):
        refused = isinstance(result.get("scope"), dict) and result["scope"].get("decision") == "not_checked"
        part.update(status="refused" if refused else "error", error=str(result.get("error") or "")[:500])
        notes.append(f"{label}this part failed ({part['error']}). Say so; never estimate it.")
        return part, notes

    rows = result.get("data") or []
    kind, columns = _classify(rows)
    kept = rows[:GROUP_CAP]
    total = result.get("total")
    truncated = bool(result.get("truncated")) or len(rows) > GROUP_CAP
    part.update(kind=kind, columns=columns, groups=kept, truncated=truncated,
                status="empty" if matched_nothing(result) else "ok")
    if kind == "count":
        one = len(columns) == 1
        part.update(group_count=1, sum_of_group_counts=rows[0][columns[0]] if one else None,
                    groups_may_overlap=False if one else None)
    elif kind == "breakdown":
        count = columns[-1] if columns else None
        part.update(
            group_count=total if truncated and isinstance(total, int) else len(rows),
            sum_of_group_counts=sum(row[count] for row in kept) if count else 0,
            # Whether one sample may be counted in two groups; the rows cannot rule it out once there are two.
            groups_may_overlap=len(kept) > 1,
            null_group=sum(row[count] for row in kept if any(row[c] is None for c in columns[:-1])) if count else 0,
        )
    else:
        part["group_count"] = total if truncated and isinstance(total, int) else len(rows)
        notes.append(f"{label}the query returned records rather than counts: read them as a list, not as a "
                     "breakdown, and do not add them up.")
    if truncated:
        of = f"{part['group_count']:,}" if isinstance(part["group_count"], int) else "more"
        notes.append(f"{label}only the first {len(kept):,} of {of} groups are returned; sum_of_group_counts and "
                     "null_group cover those groups only.")
    if answer.retry_changed_answer:
        notes.append(f"{label}{RETRY_CHANGED_ANSWER_NOTE}")
    return part, notes


def _run_part(k: int, text: str, *, label: str, question: str, multi: bool, config: Any, session: Any,
              write_gate: Callable, exec_fn: Callable, entity_out: Any, uid_note: str | None, started: float,
              deadline: float) -> tuple[dict, list[str]]:
    """One part through the graph op's chain; never raises (a failure is an ``error`` part)."""
    try:
        answer = run_graph_question(
            text, config=config, session=session, write_gate=write_gate, neo4j_exec=exec_fn,
            entity_out=entity_out, refine_context=_brief(question, multi, uid_note), prepare_cypher=cap_rows,
            retry=lambda result, cypher: _retry_for(result, cypher, deadline),
            started=started, clock=_clock, fallback_budget_s=OP_DEADLINE_S - MIN_REMAINING_S,
        )
        return _shape_part(k, text, answer, label)
    except Exception as exc:  # one part's failure never takes the others down
        part = _blank_part(k, text, "error")
        part["error"] = f"{type(exc).__name__}: {exc}"[:500]
        return part, [f"{label}this part failed ({part['error']}). Say so; never estimate it."]


def _prelude(config: Any, question: str, parts: list[str], exec_fn: Callable) -> tuple[Any, str | None, list[str]]:
    """The vocabulary, resolved once over the question and every part, and the UID check (one read-only query)."""
    from chat_nextseek.helpers.uid_check import check_uids, uid_notes, uids_in
    from chat_nextseek.portable import entity_agent

    text = question if parts == [question] else question + "\n\nParts:\n" + "\n".join(f"- {p}" for p in parts)
    entity_out = entity_agent(config, text)
    try:
        uids = uids_in(text)
        checks = check_uids(config, uids, run=exec_fn) if uids else []
        agent_note, reply_notes = uid_notes(checks)
    except Exception:  # a failed check claims nothing either way
        agent_note, reply_notes = None, []
    return entity_out, agent_note, reply_notes


def _wait_until(futures: set, deadline: float) -> set:
    """The futures still unfinished at the deadline (none when every one finished first)."""
    pending = set(futures)
    while pending:
        remaining = deadline - _clock()
        if remaining <= 0:
            break
        _, pending = wait(pending, timeout=min(remaining, _POLL_S), return_when=FIRST_COMPLETED)
    return pending


def run_aggregate(args: dict, *, config: Any, session: Any, write_gate: Callable,
                  neo4j_exec: Callable | None = None) -> dict:
    """``{question, complete, elapsed_s, deadline_s, parts, notes}``; see the module docstring."""
    question = str(args.get("query") or "").strip()
    if not question:
        raise OpValidationError("query is required")
    parts = parse_parts(args.get("parts"), question)
    multi = len(parts) > 1
    exec_fn = neo4j_exec
    if exec_fn is None:
        from chat_nextseek.helpers import tool_neo4j_query
        exec_fn = tool_neo4j_query

    started = _clock()
    deadline = started + OP_DEADLINE_S
    notes: list[str] = []
    answered: dict[int, dict] = {}
    vocabulary_late = False
    pool = ThreadPoolExecutor(max_workers=len(parts), thread_name_prefix="nextseek-aggregate")
    try:
        prelude = pool.submit(_prelude, config, question, parts, exec_fn)
        if _wait_until({prelude}, deadline):
            vocabulary_late = True
            notes.append(f"The question's vocabulary was not resolved within {OP_DEADLINE_S:.0f} s, so no part "
                         "has an answer. Say so; never estimate one.")
        elif deadline - _clock() < MIN_REMAINING_S:
            prelude.result()  # an entity agent failure still fails the op
            vocabulary_late = True  # a part started now could only time out, after spending its model calls
            notes.append(f"Resolving the question's vocabulary left under {MIN_REMAINING_S:.0f} s of the op's "
                         f"{OP_DEADLINE_S:.0f} s, so no part was started and none has an answer. Say so; never "
                         "estimate one.")
        else:
            entity_out, uid_note, uid_reply_notes = prelude.result()  # an entity agent failure fails the op
            notes.extend(uid_reply_notes)
            futures = {
                pool.submit(_run_part, k, text, label=f"Part {k}: " if multi else "", question=question,
                            multi=multi, config=config, session=session, write_gate=write_gate, exec_fn=exec_fn,
                            entity_out=entity_out, uid_note=uid_note, started=started, deadline=deadline): k
                for k, text in enumerate(parts, 1)
            }
            unfinished = _wait_until(set(futures), deadline)
            part_notes: dict[int, list[str]] = {}
            for future, k in futures.items():
                if future not in unfinished:
                    answered[k], part_notes[k] = future.result()
            for k in sorted(part_notes):
                notes.extend(part_notes[k])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    out_parts = []
    for k, text in enumerate(parts, 1):
        if k in answered:
            out_parts.append(answered[k])
            continue
        out_parts.append(_blank_part(k, text, "timed_out"))
        if vocabulary_late:
            continue
        if multi:
            notes.append(f"Part {k} did not finish within {OP_DEADLINE_S:.0f} s and has no answer. Say so; never "
                         "estimate it.")
        else:
            notes.append(f"The question did not finish within {OP_DEADLINE_S:.0f} s and has no answer. Say so; "
                         "never estimate it.")
    return {
        "question": question,
        "complete": all(part["status"] != "timed_out" for part in out_parts),
        "elapsed_s": round(_clock() - started, 1),
        "deadline_s": OP_DEADLINE_S,
        "parts": out_parts,
        "notes": notes,
    }
