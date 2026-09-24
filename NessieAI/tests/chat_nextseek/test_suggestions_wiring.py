"""The reviewer's suggestion rides on ``debug.suggestions``, and a click on it is recorded on the next turn (Task C2).

A graph turn whose review is a ``suggest`` with a suggestion that passes the guardrails (``helpers/suggestions.py``)
carries it as a chip in ``debug.suggestions`` and remembers it in the session for the turn it was offered on: the id
``append_turn`` gave that turn in ``chat_log``. The next NS turn, before it is routed anywhere, asks ``accept``
whether its text is exactly a chip's query and clears the offer either way. A click is recorded in
``debug.suggestion_accepted`` and offers no chip of its own, so chips never chain. Any turn written in between, a
Container-CC turn included, moves the newest turn id and cancels the offer.

The graph turn runs over the B3 harness's stubs (``test_graph_review_wiring``): no model, Neo4j or network call.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from chat_nextseek import chat_memory
from chat_nextseek import orchestrator as orch
from chat_nextseek.helpers import suggestions as sg
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan
from NessieAI.cc.cc_turn_complete import TurnCompletePayload, apply_turn_to_extra_state
from NessieAI.tests.chat_nextseek.test_graph_review_wiring import (  # noqa: F401  (graph_turn_harness is a fixture)
    CONVERTER_CYPHER,
    CONVERTER_Q,
    CONVERTER_ROWS,
    MEMBER,
    NHP_CYPHER,
    QUERY_COMPLETE_KEYS,
    graph_turn_harness,
    install_graph_turn_stubs,
)
from nextseek_api.assistant.session_adapter import DictSessionAdapter

CREDS = {"api_user": "someone", "api_pass": "secret"}
CHIP_QUERY = "show samples for human subjects classified as Converter"
CHIP = {"source": "reviewer", "kind": "value_split", "label": "Only Converter", "query": CHIP_QUERY,
        "reason": "The matched values were: Non-converter 57, Converter 32, Reverter 9.", "expected_count": 32}
NHP_ROWS = [{"n": 725}]


def _query_complete(events):
    return [data for name, data in events if name == "query_complete"]


def _pending(session):
    return session.get(sg.SESSION_KEY)


# --------------------------------------------------------------------------- #
# One NS turn through run_query
# --------------------------------------------------------------------------- #

class _TurnConfig:
    MIN_SAMPLETYPES: list = []
    MIN_ASSAYS: list = []
    MODEL_MODE = "test"


def _dict_session():
    return {}


def _adapter_session():
    """The request path's session: a ``DictSessionAdapter``, whose ``pop`` the chip bookkeeping relies on."""
    return DictSessionAdapter(SimpleNamespace(results_history=[], last_debug={}, extra_state={}))


SESSIONS = [_dict_session, _adapter_session]
SESSION_IDS = ["dict", "DictSessionAdapter"]


@dataclass
class NSTurn:
    debug: dict
    payload: dict
    events: list
    accept_calls: list


@pytest.fixture
def ns_turn(monkeypatch, tmp_path):
    """One NS turn through ``run_query`` on the session the test hands it, with the real ``append_turn``.

    The parser routes it to ``mode`` (a graph question unless told otherwise) and the graph turn runs over
    ``install_graph_turn_stubs``. Every ``accept`` call is recorded with the turn the pending offer was for."""

    def run(session, text, *, cypher=CONVERTER_CYPHER, rows=CONVERTER_ROWS, mode="graph_query",
            wizard_active=False):
        events: list = []
        accept_calls: list = []
        real_accept = orch.accept

        def _accept(s, user_text, *, last_turn_id):
            pending = s.get(sg.SESSION_KEY)
            got = real_accept(s, user_text, last_turn_id=last_turn_id)
            accept_calls.append({"for_turn": (pending or {}).get("for_turn"), "last_turn_id": last_turn_id,
                                 "accepted": got})
            return got

        with monkeypatch.context() as m:
            install_graph_turn_stubs(m, cypher=cypher, rows=rows, keep_append_turn=True)
            m.setattr(orch, "accept", _accept, raising=False)
            m.setattr(orch.pipeline_agent, "is_active", lambda session: wizard_active)
            m.setattr(orch.pipeline_agent, "handle_turn",
                      lambda session, config, user_text, log_dir=None: {"action": "ask", "reply": "Which genome?"})
            m.setattr(orch.pipeline_agent, "snapshot_for_chat_log", lambda session: {})
            m.setattr(orch, "_ensure_query_log_dir", lambda session, config: str(tmp_path))
            m.setattr(orch, "ArtifactStore",
                      lambda log_dir: SimpleNamespace(register_path=lambda **k: None, write_json=lambda **k: None))
            m.setattr(orch, "shortlist_catalog", lambda *a, **k: ([], [], {}))
            m.setattr(orch, "entity_agent", lambda *a, **k: EntityAgentOutput())
            m.setattr(orch, "parser_agent", lambda *a, **k: ParserPlan(mode=mode, intent_summary=text))
            payload = orch.run_query(session, _TurnConfig(), text,
                                     lambda name, data=None: events.append((name, data)),
                                     credentials=CREDS, graph_scope=MEMBER)
        return NSTurn(debug=payload["debug"], payload=payload, events=events, accept_calls=accept_calls)

    return run


def _cc_turn(session, text):
    """A Container-CC turn, written into ``chat_log`` the way ``NessieAI/cc`` writes it
    (``apply_turn_to_extra_state``), as the next NS turn reads it back after its reload."""
    payload = TurnCompletePayload(chat_session=None, user_query=text, assistant_reply="Here is the plot.",
                                  ts="2026-09-24T12:00:00+00:00", artifacts=None, cc_traces=[],
                                  turn_id="b8a4c3de-cc-run", cc_session_id=None, raw_jsonl=b"")
    extra_state = apply_turn_to_extra_state({"chat_log": session.get(chat_memory.CHAT_LOG_KEY)}, payload)
    session[chat_memory.CHAT_LOG_KEY] = extra_state[chat_memory.CHAT_LOG_KEY]
    return extra_state[chat_memory.CHAT_LOG_KEY][-1]


# --------------------------------------------------------------------------- #
# The graph turn: which reviews make a chip
# --------------------------------------------------------------------------- #

def test_a_converter_turn_carries_one_chip(graph_turn_harness):
    out = graph_turn_harness(question=CONVERTER_Q, cypher=CONVERTER_CYPHER, rows=CONVERTER_ROWS)
    bundle_id = out.payload["bundle_id"]
    assert out.debug["suggestions"] == [{"id": f"b{bundle_id}-r0", **CHIP}]
    # debug.suggestions only: the query_complete event gains no top-level key
    [event] = _query_complete(out.events)
    assert set(event) <= QUERY_COMPLETE_KEYS
    assert event["debug"]["suggestions"] == out.debug["suggestions"]
    # the chip is the reviewer's: with the reviewer out, the same turn offers none
    assert "suggestions" not in out.debug_without_reviewer


def test_an_ok_turn_carries_no_chip(graph_turn_harness):
    out = graph_turn_harness(question="How many NHP samples are there?", cypher=NHP_CYPHER, rows=NHP_ROWS)
    assert out.debug["graph_review"]["verdict"] == "ok"
    assert "suggestions" not in out.debug


def test_a_failed_query_carries_no_chip(graph_turn_harness):
    out = graph_turn_harness(question="How many NHP samples are there?", cypher=NHP_CYPHER, rows=[], ok=False)
    assert out.debug["graph_review"]["verdict"] == "note"
    assert "suggestions" not in out.debug


@pytest.mark.parametrize("make_session", SESSIONS, ids=SESSION_IDS)
def test_a_stale_review_in_the_session_makes_no_chip(ns_turn, make_session):
    """Chips come from this turn's review only: session["_graph_review"] outlives the turn that wrote it."""
    session = make_session()
    ns_turn(session, CONVERTER_Q)
    assert session.get("_graph_review")["verdict"] == "suggest"
    ok = ns_turn(session, "How many NHP samples are there?", cypher=NHP_CYPHER, rows=NHP_ROWS)
    assert ok.debug["graph_review"]["verdict"] == "ok"
    assert "suggestions" not in ok.debug
    assert not _pending(session)


# --------------------------------------------------------------------------- #
# The offer and the click
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("make_session", SESSIONS, ids=SESSION_IDS)
def test_the_offer_is_kept_for_the_turn_the_graph_turn_is_stored_under(ns_turn, make_session):
    session = make_session()
    session[chat_memory.CHAT_LOG_KEY] = [{"turn_id": 7, "user_query": "a", "mode": "graph_query"},
                                         {"turn_id": 4, "user_query": "b", "mode": "cc"}]
    first = ns_turn(session, CONVERTER_Q)
    [chip] = first.debug["suggestions"]
    [entry] = [e for e in session.get(chat_memory.CHAT_LOG_KEY) if e.get("bundle_id") == first.payload["bundle_id"]]
    assert entry["turn_id"] == 8     # append_turn numbers by the largest id, not the last entry's
    assert _pending(session) == {"for_turn": entry["turn_id"], "items": [chip]}


@pytest.mark.parametrize("make_session", SESSIONS, ids=SESSION_IDS)
def test_a_click_is_recorded_and_offers_no_new_chip(ns_turn, make_session):
    session = make_session()
    first = ns_turn(session, CONVERTER_Q)
    [chip] = first.debug["suggestions"]
    assert chip["query"] == CHIP_QUERY

    second = ns_turn(session, CHIP_QUERY)
    assert second.accept_calls == [{"for_turn": 1, "last_turn_id": 1, "accepted": chip}]
    assert second.debug["suggestion_accepted"] == {"id": chip["id"], "source": "reviewer", "kind": "value_split"}
    # the reviewer still reads the result and the chatter still gets its note, but no chip chains off a click
    assert second.debug["graph_review"]["verdict"] == "suggest"
    assert "suggestions" not in second.debug
    assert not _pending(session)
    [event] = _query_complete(second.events)
    assert set(event) <= QUERY_COMPLETE_KEYS
    assert event["debug"]["suggestion_accepted"] == second.debug["suggestion_accepted"]

    # the same text again, with nothing pending, is an ordinary turn: not a click, and it offers the chip afresh
    third = ns_turn(session, CHIP_QUERY)
    assert third.accept_calls == [{"for_turn": None, "last_turn_id": 2, "accepted": None}]
    assert "suggestion_accepted" not in third.debug
    assert [s["query"] for s in third.debug["suggestions"]] == [CHIP_QUERY]
    assert _pending(session)["for_turn"] == 3


@pytest.mark.parametrize("make_session", SESSIONS, ids=SESSION_IDS)
def test_a_cc_turn_in_between_cancels_the_offer(ns_turn, make_session):
    session = make_session()
    first = ns_turn(session, CONVERTER_Q)
    assert _pending(session)["for_turn"] == 1

    cc = _cc_turn(session, "plot those by site")
    assert cc["router_choice"] == "container_cc" and cc["turn_id"] == 2
    chat_memory.validate_chat_log_entry(cc)

    later = ns_turn(session, CHIP_QUERY)
    assert later.accept_calls == [{"for_turn": 1, "last_turn_id": 2, "accepted": None}]
    assert "suggestion_accepted" not in later.debug
    assert first.debug["suggestions"][0]["query"] == CHIP_QUERY
    # not a click, so this turn makes its own offer, for its own turn
    assert _pending(session)["for_turn"] == 3


@pytest.mark.parametrize("make_session", SESSIONS, ids=SESSION_IDS)
@pytest.mark.parametrize("route", ["unsupported", "wizard"])
def test_every_ns_turn_clears_the_offer_before_it_is_routed(ns_turn, make_session, route):
    """A turn that is not a click, whatever mode it goes to, leaves nothing pending."""
    session = make_session()
    ns_turn(session, CONVERTER_Q)
    assert _pending(session)
    other = ns_turn(session, "set up an RNA-seq run", mode="unsupported", wizard_active=route == "wizard")
    assert other.accept_calls == [{"for_turn": 1, "last_turn_id": 1, "accepted": None}]
    assert not _pending(session)
    # and the suggest review the session still holds from the graph turn makes no chip here
    assert session.get("_graph_review")["verdict"] == "suggest"
    assert "suggestions" not in other.debug


def test_a_turn_refused_for_its_identity_leaves_the_offer(ns_turn, monkeypatch):
    """The refusal asks the user to sign in and retry; it runs nothing and writes no chat_log entry, so the offer
    stands for the retry."""
    session: dict = {}
    ns_turn(session, CONVERTER_Q)
    pending = _pending(session)
    monkeypatch.setattr(orch, "accept", lambda *a, **k: pytest.fail("accept ran on a refused turn"),
                        raising=False)
    refused = orch.run_query(session, _TurnConfig(), CHIP_QUERY, None, credentials={"api_user": None,
                                                                                    "api_pass": None})
    assert refused["debug"]["identity_refused"] is True
    assert _pending(session) == pending


# --------------------------------------------------------------------------- #
# The newest turn id
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("log, expected", [
    (None, 0),
    ([], 0),
    ([{"turn_id": 1}, {"turn_id": 2}], 2),
    ([{"turn_id": 7}, {"turn_id": 4}], 7),                       # after an eviction the largest may not be last
    ([{"turn_id": 3}, {"turn_id": "0f6c-legacy-uuid"}], 3),      # a legacy CC id is skipped, never compared
    ([{"turn_id": "5"}, {"turn_id": 2}], 5),
])
def test_the_newest_turn_id_is_read_the_way_chat_memory_numbers_turns(log, expected):
    session = {} if log is None else {chat_memory.CHAT_LOG_KEY: log}
    assert orch._last_turn_id(session) == expected
    assert orch._last_turn_id(session) == chat_memory.next_turn_id(session.get(chat_memory.CHAT_LOG_KEY)) - 1
