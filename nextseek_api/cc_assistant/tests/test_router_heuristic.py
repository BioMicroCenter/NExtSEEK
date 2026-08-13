"""Tests for the router heuristic fallback (BAML-independent).

These exercise the keyword fallback used when dmac's BAML router is
unavailable. They are hermetic and zero-spend in ANY environment: the pure
heuristic cases call ``_heuristic`` directly, and the ``decide()`` fallback case
monkeypatches ``_route_query`` to report "unavailable" instead of relying on
dmac_assistant being absent -- so no real BAML/LLM call is ever made even when
dmac_assistant IS installed (e.g. the full app container).

    uv run --no-project --with pytest python -m pytest \
        nextseek_api/cc_assistant/tests/test_router_heuristic.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import router as cc_router  # noqa: E402


def test_ndma_query_routes_to_ns():
    d = cc_router._heuristic("Find me all mice treated with NDMA.")
    assert d.route == cc_router.ROUTE_NS


def test_sample_search_routes_to_ns():
    for q in [
        "What monkeys exist in the database?",
        "Find me all PBMCs associated with BTC Patients.",
        "How many samples are in the GBM study?",
        "List assays run on study X",
    ]:
        assert cc_router._heuristic(q).route == cc_router.ROUTE_NS, q


def test_code_and_file_queries_route_to_cc():
    for q in [
        "Write a Python script that plots age distribution by sex.",
        "Refactor this function to use pathlib.",
        "Read /data/projects/Foo/notes.md and summarize.",
    ]:
        assert cc_router._heuristic(q).route == cc_router.ROUTE_CC, q


def test_decide_falls_back_to_heuristic_when_baml_unavailable(monkeypatch):
    # Force the BAML layer to report "unavailable" (return None) so decide() must
    # fall through to the heuristic -- deterministically and with ZERO spend,
    # regardless of whether dmac_assistant/BAML is importable in this env.
    # (Previously this relied on dmac_assistant being absent; in the full app
    # container decide() reached the REAL BAML router and made a live LLM call,
    # returning source="baml" and failing this assertion.)
    consulted = []

    def _unavailable(query, history=None):
        consulted.append(query)
        return None  # signal "BAML router unavailable" -> decide() uses heuristic

    monkeypatch.setattr(cc_router.posterior_selector, "posterior_routing_enabled", lambda: False)
    monkeypatch.setattr(cc_router, "_route_query", _unavailable)
    d = cc_router.decide("Find me all mice treated with NDMA.")
    assert consulted == ["Find me all mice treated with NDMA."]  # decide() consulted BAML...
    assert d.route == cc_router.ROUTE_NS                          # ...then fell back to heuristic
    assert d.source == "heuristic"


def test_cc_decision_has_route_and_reasoning_fields():
    d = cc_router._heuristic("Write a shell script to tar a folder.")
    assert d.route == cc_router.ROUTE_CC
    assert d.reasoning
    assert d.source == "heuristic"
