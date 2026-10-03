"""The turn's vocabulary has one builder, ``chat_nextseek.vocabulary.resolve_vocabulary`` (plan 04, piece 3).

It is the NS turn's own prelude moved out unchanged: the lexical shortlist (50 sample types, 75 assays), then the
entity agent over it. The NS turn calls it through ``orchestrator._turn_vocabulary``, which keeps the catalog and
entity events in the order the stepper and the debug panel read them. No model is called.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat_nextseek import orchestrator as orch
from chat_nextseek import vocabulary
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan

CATALOG_ST = [{"code": "MUS"}, {"code": "NHP"}]
CATALOG_A = [{"code": "D.SEQ"}]
CREDS = {"api_user": "u", "api_pass": "p"}


def _config(**extra):
    return SimpleNamespace(MIN_SAMPLETYPES=CATALOG_ST, MIN_ASSAYS=CATALOG_A, API_USER="u", API_PASS="p", **extra)


class _NoSession:
    """A session that fails on any use: resolve_vocabulary must never read it."""

    def __getattr__(self, name):
        raise AssertionError(f"resolve_vocabulary read the session ({name})")


def test_it_shortlists_the_catalogs_then_asks_the_entity_agent_over_the_shortlist():
    order, seen = [], {}

    def shortlist(query, sampletypes, assays, **kw):
        order.append("shortlist")
        seen["shortlist"] = (query, sampletypes, assays, kw)
        return [{"code": "MUS"}], [{"code": "D.SEQ"}], {"sampletype_codes": ["MUS"], "assay_codes": ["D.SEQ"]}

    def entity(config, query, sampletypes, assays):
        order.append("entity")
        seen["entity"] = (query, sampletypes, assays)
        return EntityAgentOutput(keywords=["mice"])

    diagnostics: dict = {}
    out = vocabulary.resolve_vocabulary(_NoSession(), _config(), "how many mice", diagnostics=diagnostics,
                                        on_shortlisted=lambda: order.append("shortlisted"),
                                        entity=entity, shortlist=shortlist)

    assert out.keywords == ["mice"]
    assert order == ["shortlist", "shortlisted", "entity"]
    query, st, a, kw = seen["shortlist"]
    assert (query, st, a) == ("how many mice", CATALOG_ST, CATALOG_A)
    assert kw["k_st"] == vocabulary.SHORTLIST_SAMPLETYPES == 50
    assert kw["k_a"] == vocabulary.SHORTLIST_ASSAYS == 75
    assert seen["entity"] == ("how many mice", [{"code": "MUS"}], [{"code": "D.SEQ"}])
    assert diagnostics == {"sampletype_codes": ["MUS"], "assay_codes": ["D.SEQ"]}


def test_an_empty_shortlist_falls_back_to_the_whole_catalogs():
    seen = {}

    def entity(config, query, sampletypes, assays):
        seen["args"] = (sampletypes, assays)
        return EntityAgentOutput()

    vocabulary.resolve_vocabulary(None, _config(), "q", entity=entity, shortlist=lambda *a, **k: ([], [], {}))
    assert seen["args"] == (CATALOG_ST, CATALOG_A)


def test_the_module_defaults_are_looked_up_when_it_runs(monkeypatch):
    """The pre-run and the ops pass no agents: patching the module's own names reaches them."""
    monkeypatch.setattr(vocabulary, "shortlist_catalog", lambda *a, **k: ([], [], {}))
    monkeypatch.setattr(vocabulary, "entity_agent", lambda *a, **k: EntityAgentOutput(keywords=["x"]))
    assert vocabulary.resolve_vocabulary(None, _config(), "q").keywords == ["x"]


@pytest.fixture
def ns_turn(monkeypatch, tmp_path):
    """The real run_query up to its parser, every seam stubbed; the parser answers ``unsupported``."""
    calls = {"entity": 0, "shortlist": 0, "parser": []}
    events: list = []

    def shortlist(*a, **k):
        calls["shortlist"] += 1
        return [{"code": "MUS"}], [], {"sampletype_codes": ["MUS"], "assay_codes": []}

    def entity(config, query, sampletypes, assays):
        calls["entity"] += 1
        return EntityAgentOutput(keywords=["mice"])

    def parser(session, config, text, entity_result):
        calls["parser"].append(entity_result)
        return ParserPlan()

    monkeypatch.setattr(orch, "_identity_gate", lambda session, config, *a, **k: (config, None))
    monkeypatch.setattr(orch, "_ensure_query_log_dir", lambda session, config: str(tmp_path))
    monkeypatch.setattr(orch, "ArtifactStore", lambda log_dir: None)
    monkeypatch.setattr(orch, "_accepted_suggestion", lambda session, text: None)
    monkeypatch.setattr(orch, "_handle_pipeline_agent_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch, "shortlist_catalog", shortlist)
    monkeypatch.setattr(orch, "entity_agent", entity)
    monkeypatch.setattr(orch, "parser_agent", parser)

    def run(**kw):
        payload = orch.run_query({}, _config(), "how many mice", lambda ev, data: events.append((ev, data)),
                                 credentials=CREDS, **kw)
        return payload, events, calls
    return run


def test_the_ns_turn_resolves_its_vocabulary_through_it_with_the_same_events(ns_turn):
    payload, events, calls = ns_turn()

    assert calls["shortlist"] == 1 and calls["entity"] == 1
    assert calls["parser"][0].keywords == ["mice"]
    names = [(ev, data.get("agent")) for ev, data in events if ev in ("agent_started", "agent_complete")]
    assert names[:5] == [("agent_started", "catalog"), ("agent_complete", "catalog"),
                         ("agent_started", "entity"), ("agent_complete", "entity"), ("agent_started", "parser")]
    assert payload["debug"]["shortlist_sampletype_codes"] == ["MUS"]
    assert payload["debug"]["entity_result"]["keywords"] == ["mice"]
