"""Replay the 09-23 turns' query scope (Phase F, D2).

Every NS turn of the 2026-09-23 runs that reached the chatter with a scope, scrubbed (ids <run>-<task>, people as
Person A, B, ...). ``baseline_not_applied`` is what query_scope said before Phase F. A change to query_scope may take
NOT APPLIED items away; it may never add one.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from chat_nextseek.helpers.query_scope import describe_query_scope

HERE = pathlib.Path(__file__).parent
CONTEXT = HERE.parents[1] / "chat_nextseek" / "src" / "chat_nextseek" / "context"
FIX = json.loads((HERE / "fixtures" / "query_scope_replay.json").read_text(encoding="utf-8"))
REVIEWER_IDS = {r["id"] for r in json.loads((HERE / "fixtures" / "graph_review_replay.json").read_text(encoding="utf-8"))}
KEYS = {"id", "label", "question", "entity_result", "parser_plan", "graph_plan", "api_plan",
        "baseline_not_applied", "clears", "why"}
FALSE_CAVEATS = {"r5-631", "r5-646", "r5-650", "r5-653", "r5-667", "r5-670", "r5-678",
                 "r6-1221", "r6-1227", "r7-708", "r7-711", "m1-1349", "m2-1373", "m2-1390"}
OUT_OF_SCOPE = {"r3-602", "r4-619", "r5-659", "r5-660", "r5-662", "r7-709", "m2-1413"}
CONVERTER_WORDS = {"mtb", "infection", "positive"}


def _aliases():
    """One squashed set per row of the projects catalog, as the chatter builds it."""
    out = []
    for row in json.loads((CONTEXT / "projects_db.json").read_text(encoding="utf-8")):
        names = {re.sub(r"[^a-z0-9]", "", str(n or "").lower())
                 for n in [row.get("name"), *(row.get("alternative_names") or [])]}
        out.append(names - {""})
    return out


def _type_names():
    rows = json.loads((CONTEXT / "min_sampletypes_db.json").read_text(encoding="utf-8"))
    return {str(i["SampleType"]): str(i["Name"]) for i in rows if i.get("SampleType") and i.get("Name")}


ALIASES = _aliases()
TYPE_NAMES = _type_names()


def _scope(r):
    return describe_query_scope(entity_result=r["entity_result"], parser_plan=r["parser_plan"],
                                api_plan=r["api_plan"], graph_plan=r["graph_plan"], user_query=r["question"],
                                container_aliases=ALIASES, type_names=TYPE_NAMES)


def test_the_fixture_covers_every_reviewer_turn_and_carries_nothing_else():
    assert len(FIX) == 124
    assert REVIEWER_IDS <= {r["id"] for r in FIX}
    for r in FIX:
        assert set(r) == KEYS, r["id"]
        assert r["label"] in {"false_caveat", "out_of_scope", "quiet"}, r["id"]
        assert (r["label"] == "false_caveat") == bool(r["clears"]), r["id"]
        assert set(r["clears"]) <= set(r["baseline_not_applied"]), r["id"]


def test_the_labels_are_the_spec_table():
    assert {r["id"] for r in FIX if r["label"] == "false_caveat"} == FALSE_CAVEATS
    assert {r["id"] for r in FIX if r["label"] == "out_of_scope"} == OUT_OF_SCOPE
    assert all(not r["baseline_not_applied"] for r in FIX if r["label"] == "quiet")


@pytest.mark.parametrize("r", FIX, ids=lambda r: r["id"])
def test_no_turn_gains_a_not_applied_item(r):
    assert set(_scope(r).not_applied) <= set(r["baseline_not_applied"])


@pytest.mark.parametrize("r", [r for r in FIX if r["label"] == "out_of_scope"], ids=lambda r: r["id"])
def test_out_of_scope_caveats_are_left_alone(r):
    """-PUB (ruled not an issue), D.FILE (gone upstream), the converter keywords (operator question Q1: false, but
    fixed in the graph prompt's keyword_fields line, not by query_scope; the recorded plans predate that line),
    and m2-1413 (dev run 2026-09-29): "methylation" over :T_A_MET stays NOT APPLIED because the catalog has no name
    for A.MET, the known limit of the type-name route."""
    assert _scope(r).not_applied == r["baseline_not_applied"]


@pytest.mark.parametrize("r", [r for r in FIX if r["id"] in {"r3-602", "r7-709"}], ids=lambda r: r["id"])
def test_declared_converter_keywords_are_applied(r):
    """Operator ruling Q1, accepted offline: the same recorded plan, with Mtb, infection and positive declared in
    keyword_fields against the field the query filters (Classification), no longer reports them as NOT APPLIED."""
    words = [k for k in r["entity_result"]["keywords"] if k.lower() in CONVERTER_WORDS]
    items = {i for i in r["baseline_not_applied"] if any(f'"{w}"' in i for w in words)}
    assert len(words) == 3 and len(items) == 3, (words, r["baseline_not_applied"])
    declared = dict(r["graph_plan"]["keyword_fields"] or {}, **{w: ["Classification"] for w in words})
    after = _scope(dict(r, graph_plan=dict(r["graph_plan"], keyword_fields=declared))).not_applied
    assert after == [i for i in r["baseline_not_applied"] if i not in items] == [], after


@pytest.mark.parametrize("r", [r for r in FIX if r["label"] == "false_caveat"], ids=lambda r: r["id"])
def test_a_false_caveat_from_the_09_23_runs_is_gone(r):
    left = set(_scope(r).not_applied)
    assert not left & set(r["clears"]), sorted(left)
