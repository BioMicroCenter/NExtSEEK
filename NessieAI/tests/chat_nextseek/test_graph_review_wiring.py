"""The graph turn reviews its result before the chatter writes the reply (Task B3).

``_execute_graph_turn`` hands the result it keeps to the graph reviewer (``graph_review.review_tier1`` over the
catalog values the caller can see, then ``graph_review_counts.run_tier2`` when a check with a count variant fired),
records the review in ``debug.graph_review`` and ``session["_graph_review"]``, and on a ``note`` or ``suggest``
hands the chatter one templated note (``REVIEW_NOTE``) carrying the review's facts.

The rule these tests pin hardest: a reviewer exception, a count variant that times out, or a count variant the scope
prover refuses leaves the turn exactly as it would have been without that part of the reviewer.

Every agent and tool is stubbed; no model, Neo4j or network call is made. The catalog is a ``DictCatalog`` patched
in for ``live_values``, so no catalog read and no values query reaches the stubbed ``tool_neo4j_query``.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from chat_nextseek import graph_review_counts as counts
from chat_nextseek import orchestrator as orch
from chat_nextseek.cypher_scope import Refused, scope_cypher
from chat_nextseek.graph_review import DictCatalog, GraphReview
from chat_nextseek.graph_scope import SCOPE_ATTR, SCOPE_PARAM, GraphScope
from chat_nextseek.helpers.query_scope import _clean_note
from chat_nextseek.helpers.tools import neo4j as neo4j_tool
from chat_nextseek.helpers.tools.neo4j import SCOPE_REFUSED, is_scope_refusal
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan

MEMBER = GraphScope.for_projects([2], source="test")

CATALOG = {
    "T_PAT.Classification": [["Non-converter", 57], ["Converter", 32], ["Reverter", 9]],
    "T_IMG.FileType": [["tiff", 1306], ["tif", 212], ["TIF", 38]],
}

CONVERTER_Q = "show samples for human subjects who convert to Mtb infection positive"
CONVERTER_CYPHER = ("MATCH (s:T_PAT) WHERE toLower(toString(s.Classification)) CONTAINS 'convert' "
                    "RETURN s.id AS id, s.Classification AS Classification")
CONVERTER_ROWS = [{"id": i, "Classification": c}
                  for i, c in enumerate(["Non-converter"] * 57 + ["Converter"] * 32 + ["Reverter"] * 9)]

TIFF_Q = "How many TIFF images are there?"
TIFF_CYPHER = "MATCH (s:T_IMG) WHERE toLower(s.FileType) CONTAINS $term RETURN count(s) AS n"
TIFF_PARAMS = {"term": "tiff"}
TIFF_ROWS = [{"n": 1306}]
TIFF_FACT = "The search matched 'tiff' only; stored values also include 'tif' and 'TIF'."

NHP_CYPHER = "MATCH (s:T_NHP) RETURN count(s) AS n"

THE_NOTE = ("What the result matched: {facts} State this plainly in the first sentences. "
            "Do not mention a review or a second query.")
#: A failed query matched nothing, so its note has its own lead-in.
BREAKAGE_NOTE = ("What went wrong: {facts} State this plainly in the first sentences. "
                 "Do not mention a review or a second query.")
QUERY_COMPLETE_KEYS = {"reply", "debug", "bundle_id", "artifacts", "files"}
OK_REVIEW = GraphReview("ok", [], None, None, [], 0)
REAL_TOOL = object()


# --------------------------------------------------------------------------- #
# Results as the tool returns them
# --------------------------------------------------------------------------- #

def _scope_record(decision, codes=()):
    return {"decision": decision, "source": "test", "project_ids": [2], "injected": [], "joined": [],
            "codes": list(codes), "reasons": []}


def _graph_result(cypher, parameters, rows, *, total=None, ok=True, error=None):
    """What tool_neo4j_query returns for a member: the scoped statement and the server's scope parameter."""
    scoped_params = {**dict(parameters or {}), SCOPE_PARAM: [2]}
    if not ok:
        return {"ok": False, "error": error or "Neo.ClientError.Statement.SyntaxError", "data": None,
                "cypher": cypher + " /* scoped */", "submitted_cypher": cypher, "parameters": scoped_params,
                "scope": _scope_record("proven")}
    return {"ok": True, "data": rows, "count": len(rows), "total": len(rows) if total is None else total,
            "truncated": False, "limit": None, "cypher": cypher + " /* scoped */", "submitted_cypher": cypher,
            "parameters": scoped_params, "counters": {}, "scope": _scope_record("proven")}


def _proving_count_tool(seen, total=1556):
    """A count tool that runs the real scope prover for a member on what it is handed, then answers ``total``."""
    def tool(config, cypher, parameters=None, *, timeout_s=None, total_only=False):
        seen.append({"cypher": cypher, "parameters": dict(parameters or {}), "timeout_s": timeout_s,
                     "total_only": total_only})
        outcome = scope_cypher(cypher, parameters, MEMBER)
        if isinstance(outcome, Refused):
            return {"ok": False, "error": f"{SCOPE_REFUSED} Reasons: {'; '.join(outcome.reasons)}.", "data": None,
                    "cypher": cypher, "submitted_cypher": cypher, "parameters": dict(parameters or {}),
                    "scope": _scope_record("refused", outcome.codes)}
        return {"ok": True, "data": [], "count": None, "total": total, "truncated": False, "limit": None,
                "cypher": outcome.cypher, "submitted_cypher": cypher, "parameters": outcome.parameters,
                "counters": {}, "scope": _scope_record(outcome.decision)}
    return tool


# --------------------------------------------------------------------------- #
# The harness
# --------------------------------------------------------------------------- #

@dataclass
class Turn:
    reply: str
    query_notes: list
    debug: dict
    session: dict
    events: list
    payload: dict
    live_values_calls: list
    count_calls: list


@dataclass
class Out(Turn):
    reply_without_reviewer: str = ""
    notes_without_reviewer: list | None = None
    debug_without_reviewer: dict | None = None
    payload_without_reviewer: dict | None = None
    reply_tier1_only: str = ""
    notes_tier1_only: list | None = None
    debug_tier1_only: dict | None = None


def _comparable(debug: dict) -> dict:
    """The debug payload without the review and without the timings, which differ between two runs."""
    out = {k: v for k, v in debug.items() if k != "graph_review"}
    out["graph_attempts"] = [{k: v for k, v in a.items() if k != "elapsed_ms"} for a in debug["graph_attempts"]]
    return out


@dataclass
class GraphStubs:
    """What the stubs of one graph turn saw: the notes the chatter was handed, and the calls to the catalog
    provider and the count tool."""
    notes: list | None = None
    live_values_calls: list = field(default_factory=list)
    count_calls: list = field(default_factory=list)


def install_graph_turn_stubs(m, *, cypher, rows, parameters=None, total=None, ok=True, error=None, catalog=None,
                             count_tool=None, live_values=None, keep_append_turn=False) -> GraphStubs:
    """Patch, through ``m`` (a ``monkeypatch.context()``), every agent and tool ``_execute_graph_turn`` calls.

    The graph agent writes ``cypher`` with ``parameters``; Neo4j answers ``rows`` as the tool does for a member;
    the chatter writes every note it is handed into the reply, so two replies are equal exactly when the chatter
    was handed the same notes; the catalog is a ``DictCatalog`` over ``catalog`` (``CATALOG`` when None); a count
    variant goes to ``count_tool``, the proving stub when None, the real tool for ``REAL_TOOL``. ``append_turn`` is
    a no-op unless ``keep_append_turn``, for a caller that needs the turn in ``chat_log``."""
    seen = GraphStubs()
    plan = GraphAgentPlan(cypher=cypher, parameters=dict(parameters or {}), context_mode="catalog")
    catalog = CATALOG if catalog is None else catalog

    def _agent(config, user_text, entity_result, parser_plan, retry_context=None, refine_context=None):
        return plan

    def _neo4j(config, cy, params=None, **kwargs):
        return _graph_result(cy, params, rows, total=total, ok=ok, error=error)

    def _chatter(*a, **k):
        seen.notes = list(k.get("query_notes") or [])
        return "reply" + "".join(f" [{n}]" for n in seen.notes)

    def _live_values(config, **kwargs):
        seen.live_values_calls.append(kwargs)
        return DictCatalog(catalog)

    m.setattr(orch, "graph_agent", _agent)
    m.setattr(orch, "tool_neo4j_query", _neo4j)
    m.setattr(orch, "chatter_agent_answer", _chatter)
    if not keep_append_turn:
        m.setattr(orch, "append_turn", lambda *a, **k: None)
    m.setattr(orch, "live_values", live_values or _live_values, raising=False)
    if count_tool is not REAL_TOOL:
        m.setattr(counts, "tool_neo4j_query", count_tool or _proving_count_tool(seen.count_calls))
    return seen


@pytest.fixture
def graph_turn_harness(monkeypatch, tmp_path):
    """Run ``_execute_graph_turn`` three times over the same stubs (``install_graph_turn_stubs``): as it is, with
    the reviewer out (``_review_graph_turn`` answering an empty ok review), and with Tier 1 alone (``run_tier2`` a
    no-op)."""

    def one(*, question, cypher, rows, parameters, total, ok, error, catalog, count_tool, turn_age_s, live_values,
            extra):
        events: list = []
        with monkeypatch.context() as m:
            seen = install_graph_turn_stubs(m, cypher=cypher, rows=rows, parameters=parameters, total=total, ok=ok,
                                            error=error, catalog=catalog, count_tool=count_tool,
                                            live_values=live_values)
            for name, value in extra.items():
                m.setattr(orch, name, value, raising=False)
            config = SimpleNamespace(MODEL_MODE="test", **{SCOPE_ATTR: MEMBER})
            session: dict = {}
            debug: dict = {}
            payload = orch._execute_graph_turn(
                config=config, session=session, user_text=question, entity_result=EntityAgentOutput(),
                plan=ParserPlan(mode="graph_query", intent_summary=question), log_dir=str(tmp_path),
                artifact_store=SimpleNamespace(register_path=lambda **k: None, write_json=lambda **k: None),
                send_event=lambda name, data=None: events.append((name, data)), debug_payload=debug,
                t_total_start=time.perf_counter() - turn_age_s,
            )
        return Turn(reply=payload["reply"], query_notes=seen.notes, debug=debug, session=session,
                    events=events, payload=payload, live_values_calls=seen.live_values_calls,
                    count_calls=seen.count_calls)

    def run(*, question, cypher, rows, parameters=None, total=None, ok=True, error=None, catalog=None,
            count_tool=None, turn_age_s=0.0, live_values=None):
        kw = dict(question=question, cypher=cypher, rows=rows, parameters=parameters, total=total, ok=ok,
                  error=error, catalog=CATALOG if catalog is None else catalog, count_tool=count_tool,
                  turn_age_s=turn_age_s, live_values=live_values)
        main = one(**kw, extra={})
        without = one(**kw, extra={"_review_graph_turn": lambda *a, **k: OK_REVIEW})
        tier1 = one(**kw, extra={"run_tier2": lambda config, inp, review, **k: review})
        return Out(**vars(main),
                   reply_without_reviewer=without.reply, notes_without_reviewer=without.query_notes,
                   debug_without_reviewer=without.debug, payload_without_reviewer=without.payload,
                   reply_tier1_only=tier1.reply, notes_tier1_only=tier1.query_notes, debug_tier1_only=tier1.debug)

    return run


def _query_complete(events):
    return [data for name, data in events if name == "query_complete"]


# --------------------------------------------------------------------------- #
# The brief's three
# --------------------------------------------------------------------------- #

def test_converter_turn_carries_a_graph_review_and_a_note(graph_turn_harness):
    out = graph_turn_harness(question=CONVERTER_Q, cypher=CONVERTER_CYPHER, rows=CONVERTER_ROWS)
    assert out.debug["graph_review"]["verdict"] == "suggest"
    assert any("Non-converter 57" in n for n in out.query_notes)


def test_ok_review_adds_no_note(graph_turn_harness):
    out = graph_turn_harness(question="How many NHP samples are there?", cypher=NHP_CYPHER, rows=[{"n": 725}])
    assert out.debug["graph_review"]["verdict"] == "ok"
    assert out.query_notes == []


def test_reviewer_exception_leaves_the_turn_unchanged(graph_turn_harness, monkeypatch):
    monkeypatch.setattr(orch, "review_tier1", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
                        raising=False)
    out = graph_turn_harness(question="q", cypher=NHP_CYPHER, rows=[{"n": 725}])
    assert out.debug["graph_review"]["verdict"] == "ok" and "boom" in out.debug["graph_review"]["error"]
    assert out.reply == out.reply_without_reviewer
    assert out.query_notes == out.notes_without_reviewer
    assert _comparable(out.debug) == _comparable(out.debug_without_reviewer)


# --------------------------------------------------------------------------- #
# Review Focus 1: a count variant that fails leaves Tier 1's turn as it was
# --------------------------------------------------------------------------- #

def _assert_tier1_turn(out):
    """The reply, the notes and the debug payload are Tier 1's; the variant is recorded as failed, no number."""
    assert out.reply == out.reply_tier1_only
    assert out.query_notes == out.notes_tier1_only
    assert _comparable(out.debug) == _comparable(out.debug_tier1_only)
    review, tier1 = out.debug["graph_review"], out.debug_tier1_only["graph_review"]
    assert review["verdict"] == tier1["verdict"] == "suggest"
    assert review["disclosure"] == tier1["disclosure"] == TIFF_FACT
    assert review["suggestion"] == tier1["suggestion"]
    assert "expected_count" not in review["suggestion"]
    assert len(review["variants"]) == 1
    assert review["variants"][0]["ok"] is False and review["variants"][0]["total"] is None
    assert tier1["variants"] == []
    assert out.query_notes == [THE_NOTE.format(facts=TIFF_FACT)]


@pytest.mark.parametrize("how", ["returned", "raised"])
def test_a_count_variant_that_times_out_leaves_tier_1s_turn(graph_turn_harness, how):
    seen = []

    def timing_out(config, cypher, parameters=None, *, timeout_s=None, total_only=False):
        seen.append({"timeout_s": timeout_s, "total_only": total_only})
        message = ("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration: The transaction has been "
                   "terminated. Retry your operation in a new transaction.")
        if how == "raised":
            raise TimeoutError(message)
        return {"ok": False, "error": message, "data": None, "cypher": cypher, "submitted_cypher": cypher,
                "parameters": dict(parameters or {}), "scope": _scope_record("proven")}

    out = graph_turn_harness(question=TIFF_Q, cypher=TIFF_CYPHER, parameters=TIFF_PARAMS, rows=TIFF_ROWS,
                             count_tool=timing_out)
    # The variant was really attempted, bounded, as a total only (seen holds all three runs' calls but the
    # no-reviewer and Tier-1-only runs make none).
    assert len(seen) == 1 and seen[0]["total_only"] is True and 1 <= seen[0]["timeout_s"] <= 5
    _assert_tier1_turn(out)


def test_a_count_variant_the_scope_prover_refuses_leaves_tier_1s_turn(graph_turn_harness, monkeypatch):
    refusals = []

    def refusing(cypher, parameters, scope):
        refusals.append(cypher)
        return Refused(codes=("label_not_allowed",), reasons=("line 1, column 8: this label is not allowed",))

    # The real tool: its write check, then the scope on the config, then the prover, which refuses.
    monkeypatch.setattr(neo4j_tool, "scope_cypher", refusing)
    out = graph_turn_harness(question=TIFF_Q, cypher=TIFF_CYPHER, parameters=TIFF_PARAMS, rows=TIFF_ROWS,
                             count_tool=REAL_TOOL)
    assert len(refusals) == 1
    _assert_tier1_turn(out)


def test_the_refusal_really_is_a_scope_refusal(monkeypatch):
    """Guards the test above: the real tool reports that prover verdict as a scope refusal, opening no driver."""
    monkeypatch.setattr(neo4j_tool, "scope_cypher",
                        lambda c, p, s: Refused(codes=("label_not_allowed",), reasons=("no",)))
    config = SimpleNamespace(**{SCOPE_ATTR: MEMBER})
    got = counts.tool_neo4j_query(config, "MATCH (s:T_IMG) RETURN 1 AS n", {}, timeout_s=5, total_only=True)
    assert got["ok"] is False and is_scope_refusal(got)


def test_a_count_that_runs_reaches_the_note_and_the_suggestion(graph_turn_harness):
    """The variant goes to the tool as the model wrote it: the submitted statement, and its parameters without
    the server's scope parameter (the prover refuses that reserved name on the way in), so a member's count is
    proven and runs."""
    out = graph_turn_harness(question=TIFF_Q, cypher=TIFF_CYPHER, parameters=TIFF_PARAMS, rows=TIFF_ROWS)
    assert len(out.count_calls) == 1
    call = out.count_calls[0]
    assert SCOPE_PARAM not in call["parameters"] and call["parameters"] == {"term": "tif"}
    assert "/* scoped */" not in call["cypher"]
    review = out.debug["graph_review"]
    assert review["variants"][0]["ok"] is True and review["variants"][0]["total"] == 1556
    assert review["suggestion"]["expected_count"] == 1556
    facts = f"{TIFF_FACT} Every spelling of 'tif' gives 1,556."
    assert review["disclosure"] == facts
    assert out.query_notes == [THE_NOTE.format(facts=facts)]
    assert json.loads(json.dumps(out.session["_graph_review"])) == review


# --------------------------------------------------------------------------- #
# Where the review is recorded, and what it never adds
# --------------------------------------------------------------------------- #

def test_the_review_is_in_the_debug_payload_and_the_session(graph_turn_harness):
    out = graph_turn_harness(question=CONVERTER_Q, cypher=CONVERTER_CYPHER, rows=CONVERTER_ROWS)
    review = out.debug["graph_review"]
    assert set(review) == {"verdict", "checks", "disclosure", "suggestion", "variants", "elapsed_ms", "error",
                           "lookups", "fired"}
    assert out.session["_graph_review"] == review
    assert json.loads(json.dumps(review)) == review   # the Django session keeps it in a JSON column
    assert out.payload["debug"]["graph_review"] == review
    assert review["suggestion"]["label"] == "Only Converter"
    assert {c["name"] for c in review["checks"] if c["fired"]} >= {"negated_value"}
    # negated_value has no count variant, so Tier 2 never ran
    assert review["variants"] == [] and out.count_calls == []


def test_the_query_complete_event_gains_no_top_level_key(graph_turn_harness):
    out = graph_turn_harness(question=CONVERTER_Q, cypher=CONVERTER_CYPHER, rows=CONVERTER_ROWS)
    [event] = _query_complete(out.events)
    assert set(event) <= QUERY_COMPLETE_KEYS
    assert set(event) == set(out.payload_without_reviewer)
    assert "graph_review" in event["debug"]


def test_a_failed_query_gets_the_breakage_note(graph_turn_harness):
    out = graph_turn_harness(question="How many NHP samples are there?", cypher=NHP_CYPHER, rows=[], ok=False)
    review = out.debug["graph_review"]
    assert review["verdict"] == "note"
    assert out.query_notes == [BREAKAGE_NOTE.format(facts="The database query failed.")]
    assert out.count_calls == []


def test_the_review_note_is_the_template():
    assert orch.REVIEW_NOTE == THE_NOTE
    assert "\u2014" not in orch.REVIEW_NOTE and "\u2013" not in orch.REVIEW_NOTE
    assert orch.BREAKAGE_NOTE == BREAKAGE_NOTE
    assert "\u2014" not in orch.BREAKAGE_NOTE and "\u2013" not in orch.BREAKAGE_NOTE


def test_the_breakage_fact_does_not_invite_retry_narration():
    """"on its final attempt" says there were other attempts, which the reply may never narrate."""
    from chat_nextseek.graph_review import DictCatalog, ReviewInput, review_tier1
    failed = ReviewInput(question="How many NHP samples are there?", cypher=NHP_CYPHER, parameters={},
                         keyword_fields={}, rows=[], count=0, total=0, ok=False, error="boom")
    assert review_tier1(failed, DictCatalog(None)).disclosure == "The database query failed."
    no_query = ReviewInput(question="How many NHP samples are there?", cypher=None, parameters={},
                           keyword_fields={}, rows=[], count=None, total=None, ok=True, error=None)
    assert review_tier1(no_query, DictCatalog(None)).disclosure == "No database query ran for this question."


def test_a_breakage_note_is_bounded_like_the_review_note():
    note = orch._review_note("x" * 299, orch.BREAKAGE_NOTE)
    assert len(note) < 400 and _clean_note(note) == note
    assert len(note) == orch.REVIEW_NOTE_MAX, "the room is the breakage template's own, not the review note's"
    assert note.startswith("What went wrong: x")
    assert note.endswith("\u2026 State this plainly in the first sentences. Do not mention a review or a second query.")
    assert orch._review_note("The database query failed.", orch.BREAKAGE_NOTE) == BREAKAGE_NOTE.format(
        facts="The database query failed.")


def test_a_full_disclosure_still_fits_under_the_chatters_cut():
    """describe_query_scope cuts a note at 400 characters, which would drop the instruction at the note's end. A
    disclosure is at most 299 characters and the template 111, so whole facts are dropped from the end to fit."""
    facts = [f"The fact number {i} says something about the matched values here." for i in range(10)]
    disclosure = " ".join(facts)[:299].rsplit(" ", 1)[0]
    assert len(disclosure) <= 299
    note = orch._review_note(disclosure)
    assert len(note) < 400 and _clean_note(note) == note
    assert note.startswith("What the result matched: The fact number 0 says")
    assert note.endswith(" State this plainly in the first sentences. Do not mention a review or a second query.")
    kept = note[len("What the result matched: "):note.index(" State this plainly")]
    assert kept.endswith(".") and disclosure.startswith(kept)
    # a short disclosure is the template, untouched
    assert orch._review_note(TIFF_FACT) == THE_NOTE.format(facts=TIFF_FACT)


def test_one_fact_too_long_for_the_room_is_cut():
    note = orch._review_note("x" * 299)
    assert len(note) < 400 and _clean_note(note) == note
    assert note.endswith("… State this plainly in the first sentences. Do not mention a review or a second query.")


# --------------------------------------------------------------------------- #
# Time: the catalog provider, the Tier 2 gate and budget, and every attempt's elapsed_ms
# --------------------------------------------------------------------------- #

def test_every_attempt_records_its_neo4j_time(monkeypatch, tmp_path):
    """The first run and every retry: a zero-row result is retried once, and both attempts are timed."""
    monkeypatch.setattr(orch, "graph_agent",
                        lambda *a, **k: GraphAgentPlan(cypher=NHP_CYPHER, context_mode="catalog"))

    def slow_zero(config, cypher, params=None, **kwargs):
        time.sleep(0.03)
        return _graph_result(cypher, params, [])

    monkeypatch.setattr(orch, "tool_neo4j_query", slow_zero)
    monkeypatch.setattr(orch, "chatter_agent_answer", lambda *a, **k: "reply")
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch, "live_values", lambda config, **k: DictCatalog({}), raising=False)
    debug: dict = {}
    orch._execute_graph_turn(
        config=SimpleNamespace(MODEL_MODE="test", **{SCOPE_ATTR: MEMBER}), session={}, user_text="q",
        entity_result=EntityAgentOutput(), plan=ParserPlan(mode="graph_query", intent_summary="q"),
        log_dir=str(tmp_path), artifact_store=SimpleNamespace(register_path=lambda **k: None,
                                                              write_json=lambda **k: None),
        send_event=lambda *a, **k: None, debug_payload=debug, t_total_start=time.perf_counter(),
    )
    attempts = debug["graph_attempts"]
    assert [a["reason"] for a in attempts] == ["initial", "zero_rows"]
    assert all(isinstance(a["elapsed_ms"], int) and 25 <= a["elapsed_ms"] < 5000 for a in attempts)


TIFF_ZERO = _graph_result(TIFF_CYPHER, TIFF_PARAMS, [{"n": 0}])
TIFF_FOUND = _graph_result(TIFF_CYPHER, TIFF_PARAMS, TIFF_ROWS)
TIFF_TIMED_OUT = _graph_result(TIFF_CYPHER, TIFF_PARAMS, [], ok=False,
                               error="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration")
#: The two ways a retry is thrown away and the first result kept: it failed, or it also matched nothing.
DISCARDED_RETRY = {"retry_failed": TIFF_TIMED_OUT, "retry_also_empty": TIFF_ZERO}


def _scripted_turn(monkeypatch, tmp_path, *, results, times_ms):
    """One TIFF graph turn whose queries return ``results`` in order, each timed at the matching ``times_ms``."""
    queue, clock = list(results), list(times_ms)
    live_calls, count_calls = [], []
    monkeypatch.setattr(orch, "graph_agent", lambda *a, **k: GraphAgentPlan(
        cypher=TIFF_CYPHER, parameters=dict(TIFF_PARAMS), context_mode="catalog"))
    monkeypatch.setattr(orch, "tool_neo4j_query", lambda config, cy, params=None, **k: queue.pop(0))
    monkeypatch.setattr(orch, "_ms_since", lambda t0: clock.pop(0) if clock else 0)
    monkeypatch.setattr(orch, "chatter_agent_answer", lambda *a, **k: "reply")
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch, "live_values", lambda config, **k: live_calls.append(k) or DictCatalog(CATALOG))
    monkeypatch.setattr(counts, "tool_neo4j_query", _proving_count_tool(count_calls))
    debug: dict = {}
    orch._execute_graph_turn(
        config=SimpleNamespace(MODEL_MODE="test", **{SCOPE_ATTR: MEMBER}), session={}, user_text=TIFF_Q,
        entity_result=EntityAgentOutput(), plan=ParserPlan(mode="graph_query", intent_summary=TIFF_Q),
        log_dir=str(tmp_path), artifact_store=SimpleNamespace(register_path=lambda **k: None,
                                                              write_json=lambda **k: None),
        send_event=lambda *a, **k: None, debug_payload=debug, t_total_start=time.perf_counter(),
    )
    assert queue == [] and clock == []
    assert [a["elapsed_ms"] for a in debug["graph_attempts"]] == list(times_ms)
    return debug, live_calls, count_calls


@pytest.mark.parametrize("kind", sorted(DISCARDED_RETRY))
def test_a_slow_kept_statement_behind_a_fast_discarded_retry_gets_no_count(monkeypatch, tmp_path, kind):
    """The retry is thrown away, so the reviewed statement is the first one, which took over 5 s: its count
    variant (a relaxation of that statement) is skipped and Tier 1's uncached reads get the late budget."""
    debug, live_calls, count_calls = _scripted_turn(
        monkeypatch, tmp_path, results=[TIFF_ZERO, DISCARDED_RETRY[kind]], times_ms=[6000, 40])
    assert live_calls == [{"budget_s": orch.REVIEW_TIER1_LATE_BUDGET_S}]
    assert count_calls == [] and debug["graph_review"]["variants"] == []
    assert debug["graph_review"]["verdict"] == "suggest"      # Tier 1 still speaks


@pytest.mark.parametrize("kind", sorted(DISCARDED_RETRY))
def test_a_fast_kept_statement_behind_a_slow_discarded_retry_may_count(monkeypatch, tmp_path, kind):
    debug, live_calls, count_calls = _scripted_turn(
        monkeypatch, tmp_path, results=[TIFF_ZERO, DISCARDED_RETRY[kind]], times_ms=[40, 6000])
    assert live_calls == [{"budget_s": orch.REVIEW_TIER1_BUDGET_S}]
    assert len(count_calls) == 1
    assert [v["ok"] for v in debug["graph_review"]["variants"]] == [True]


def test_a_kept_retry_is_timed_as_the_retry(monkeypatch, tmp_path):
    """The retry found something and replaced the first result, so its time is the reviewed statement's."""
    debug, live_calls, count_calls = _scripted_turn(
        monkeypatch, tmp_path, results=[TIFF_ZERO, TIFF_FOUND], times_ms=[6000, 40])
    assert debug["graph_attempts"][-1]["count"] == 1 and live_calls == [{"budget_s": orch.REVIEW_TIER1_BUDGET_S}]
    assert len(count_calls) == 1


def _direct(monkeypatch, *, elapsed_ms, turn_age_s, catalog=None):
    """``_review_graph_turn`` on the TIFF turn, with live_values and run_tier2 recorded."""
    live_calls, tier2_calls = [], []

    def _live_values(config, **kwargs):
        live_calls.append(kwargs)
        return DictCatalog(CATALOG if catalog is None else catalog)

    def _run_tier2(config, inp, review, **kwargs):
        tier2_calls.append({"inp": inp, **kwargs})
        return review

    monkeypatch.setattr(orch, "live_values", _live_values, raising=False)
    monkeypatch.setattr(orch, "run_tier2", _run_tier2, raising=False)
    plan = GraphAgentPlan(cypher=TIFF_CYPHER, parameters=dict(TIFF_PARAMS))
    result = _graph_result(TIFF_CYPHER, TIFF_PARAMS, TIFF_ROWS)
    review = orch._review_graph_turn(SimpleNamespace(**{SCOPE_ATTR: MEMBER}), TIFF_Q, plan, result,
                                     elapsed_ms=elapsed_ms, t_turn_start=time.perf_counter() - turn_age_s)
    return review, live_calls, tier2_calls


def test_one_catalog_provider_per_turn_with_the_tier1_budget(monkeypatch):
    review, live_calls, tier2_calls = _direct(monkeypatch, elapsed_ms=120, turn_age_s=3)
    assert live_calls == [{"budget_s": orch.REVIEW_TIER1_BUDGET_S}]
    assert review.verdict == "suggest"
    [call] = tier2_calls
    assert call["inp"].elapsed_ms == 120            # the kept statement's time
    assert call["inp"].cypher == TIFF_CYPHER         # the statement as the model wrote it
    assert call["inp"].parameters == TIFF_PARAMS     # without the server's scope parameter
    assert 7.0 < call["budget_s"] <= 8.0


def test_a_slow_statement_gets_the_late_tier1_budget(monkeypatch):
    """Cache only there made the reviewer silent on the turns it was built for (2026-09-25)."""
    _review, live_calls, tier2_calls = _direct(monkeypatch, elapsed_ms=5001, turn_age_s=3)
    assert live_calls == [{"budget_s": orch.REVIEW_TIER1_LATE_BUDGET_S}]
    # run_tier2 is still handed the turn; it skips a statement over SKIP_AFTER_MS itself
    assert [c["inp"].elapsed_ms for c in tier2_calls] == [5001]


def test_a_turn_past_45_seconds_gets_the_late_tier1_budget_and_runs_no_count(monkeypatch):
    review, live_calls, tier2_calls = _direct(monkeypatch, elapsed_ms=120, turn_age_s=46)
    assert live_calls == [{"budget_s": orch.REVIEW_TIER1_LATE_BUDGET_S}]
    assert tier2_calls == []
    assert review.verdict == "suggest" and review.disclosure == TIFF_FACT


def test_no_count_when_the_fired_check_has_no_variant(monkeypatch):
    live_calls, tier2_calls = [], []
    monkeypatch.setattr(orch, "live_values", lambda config, **k: live_calls.append(k) or DictCatalog(CATALOG),
                        raising=False)
    monkeypatch.setattr(orch, "run_tier2", lambda *a, **k: tier2_calls.append(k), raising=False)
    plan = GraphAgentPlan(cypher=CONVERTER_CYPHER)
    review = orch._review_graph_turn(SimpleNamespace(**{SCOPE_ATTR: MEMBER}), CONVERTER_Q, plan,
                                     _graph_result(CONVERTER_CYPHER, {}, CONVERTER_ROWS),
                                     elapsed_ms=30, t_turn_start=time.perf_counter())
    assert review.verdict == "suggest" and tier2_calls == []


def test_tier_2_gets_what_tier_1_left_of_the_eight_seconds(monkeypatch):
    real_tier1 = orch.review_tier1

    def slow_tier1(inp, catalog):
        time.sleep(0.2)
        return real_tier1(inp, catalog)

    monkeypatch.setattr(orch, "review_tier1", slow_tier1)
    _review, _live, tier2_calls = _direct(monkeypatch, elapsed_ms=120, turn_age_s=3)
    [call] = tier2_calls
    assert 7.0 < call["budget_s"] <= 7.8


@pytest.mark.parametrize("broken", ["live_values", "run_tier2"])
def test_anything_escaping_the_reviewer_is_an_ok_review(graph_turn_harness, monkeypatch, broken):
    """live_values and run_tier2 never raise by contract; if either did, the whole review is ok, the turn gets no
    note, and everything else is as it would be without the reviewer."""
    def boom(*a, **k):
        raise RuntimeError(f"{broken} broke")

    if broken == "run_tier2":
        monkeypatch.setattr(orch, "run_tier2", boom, raising=False)
    out = graph_turn_harness(question=TIFF_Q, cypher=TIFF_CYPHER, parameters=TIFF_PARAMS, rows=TIFF_ROWS,
                             live_values=boom if broken == "live_values" else None)
    review = out.debug["graph_review"]
    assert review["verdict"] == "ok" and "broke" in review["error"]
    assert review["checks"] == [] and review["disclosure"] is None and review["suggestion"] is None
    assert out.reply == out.reply_without_reviewer
    assert out.query_notes == out.notes_without_reviewer == []
    assert _comparable(out.debug) == _comparable(out.debug_without_reviewer)


# --------------------------------------------------------------------------- #
# The RNA-Seq turn (dev 1276): the answer stands, the mix is named, "Only RNA-Seq" is offered
# --------------------------------------------------------------------------- #

RNA_Q = "How many TCGA patients have at least one RNA-Seq alignment derived from their samples?"
RNA_CYPHER = ("MATCH (p:T_PAT)-[:IN_STUDY]->(:Study)-[:IN_INVESTIGATION]->(inv:Investigation) "
              "WHERE inv.title = $investigation_title AND EXISTS { MATCH (aln:T_A_ALN)-[:DERIVED_FROM*1..12]->"
              "(rna:T_RNA)-[:DERIVED_FROM*1..12]->(p:T_PAT) } RETURN count(DISTINCT p) AS n")
RNA_CATALOG = {
    "T_A_ALN.*": [["DataType", 6]], "T_A_ALN.@name": [["Sequence Alignment Analysis", 91323]],
    "T_A_ALN.DataType": [["RNA-Seq", 33227], ["WGS", 23723], ["WXS", 22441], ["miRNA-Seq", 11441]],
    "T_PAT.@name": [["Patient", 12108]], "T_RNA.@name": [["RNA Sample", 18200]],
}
RNA_SPLIT = [{"value": "miRNA-Seq", "n": 10561}, {"value": "RNA-Seq", "n": 10517}, {"value": "WXS", "n": 3}]
RNA_FACTS = ("The question names 'RNA-Seq', but the search did not filter on it. Counted by DataType: miRNA-Seq "
             "10,561, RNA-Seq 10,517, WXS 3; one result can fall under more than one.")


def _split_tool(seen):
    """Proves every statement for the member, then answers the split with its rows and a count with 10,517."""
    def tool(config, cypher, parameters=None, *, timeout_s=None, total_only=False):
        seen.append(cypher)
        outcome = scope_cypher(cypher, parameters, MEMBER)
        assert not isinstance(outcome, Refused), outcome.reasons
        if cypher.endswith("RETURN value, n"):
            return {"ok": True, "data": list(RNA_SPLIT), "count": 3, "total": None}
        return {"ok": True, "data": [], "count": None, "total": 10517}
    return tool


def test_the_rna_seq_turn_names_the_mix_and_offers_only_rna_seq(graph_turn_harness):
    seen: list = []
    out = graph_turn_harness(question=RNA_Q, cypher=RNA_CYPHER, rows=[{"n": 10761}],
                             parameters={"investigation_title": "TCGA"}, catalog=RNA_CATALOG,
                             count_tool=_split_tool(seen))
    review = out.debug["graph_review"]
    assert review["verdict"] == "suggest" and review["fired"] == ["unapplied_value"]
    assert review["disclosure"] == RNA_FACTS
    assert THE_NOTE.format(facts=RNA_FACTS) in out.query_notes
    [chip] = out.debug["suggestions"]
    assert chip["label"] == "Only RNA-Seq" and chip["expected_count"] == 10517 and chip["kind"] == "narrow_value"
    assert chip["query"] == (RNA_Q + " Count only Sequence Alignment Analysis records whose DataType is RNA-Seq.")
    assert [v["edit"] for v in review["variants"]] == ["unapplied_value: split by DataType"]
    assert review["lookups"]["late"] is False and review["lookups"]["slow"] is False
    assert "turn_age_s" in review["lookups"]
    # the reply's answer is untouched: the note only adds what the result matched
    assert out.reply.startswith("reply") and out.reply_without_reviewer == "reply"
