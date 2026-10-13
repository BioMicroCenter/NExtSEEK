"""Fix 2 (a category word covers every type of that kind) and fix 3 (a zero answer beside a total is a zero)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import chat_nextseek

from chat_nextseek.agents.entity import expand_category_types
from chat_nextseek.graph_review import _premise_count
from chat_nextseek.helpers.tools.neo4j import matched_nothing, total_column_value
from chat_nextseek.schemas.entity import EntityAgentOutput, EntityItem

CATALOG = [
    {"SampleType": "X.EXP", "Name": "Expression A", "Tags": "expression, fooomic profiling"},
    {"SampleType": "X.SC", "Name": "Single cell B", "Tags": "single cell, fooomics"},
    {"SampleType": "X.RNA", "Name": "RNA C", "Tags": "fooomic, rna"},
    {"SampleType": "Y.IMG", "Name": "Image D", "Tags": "lipidomic, picture, imaging"},
    {"SampleType": "Y.MSI", "Name": "Mass image E", "Tags": "lipidomics mass, imaging"},
    {"SampleType": "Z.SEQ", "Name": "Seq F", "Tags": "metagenome, sequencing"},
    {"SampleType": "Z.ALN", "Name": "Align G", "Tags": "metagenome alignment, sequencing"},
    {"SampleType": "M.MUS", "Name": "Mouse", "Tags": "mouse, mice"},
]


def entity(*codes):
    return EntityAgentOutput(sampletypes=[EntityItem(code=c) for c in codes])


@pytest.mark.parametrize("query,start,word,expect", [
    ("how many mice have fooomic data", "X.EXP", "fooomic", {"X.EXP", "X.SC", "X.RNA"}),
    ("samples with lipidomic data", "Y.IMG", "lipidomic", {"Y.IMG", "Y.MSI"}),
    ("any metagenome data for these", "Z.SEQ", "metagenome", {"Z.SEQ", "Z.ALN"}),
])
def test_category_word_covers_every_member(query, start, word, expect):
    out = entity("M.MUS", start)
    expand_category_types(query, out, CATALOG)
    assert {i.code for i in out.sampletypes} == expect | {"M.MUS"}
    assert set(out.category_types[word]) == expect


def test_a_named_type_stays_one_type():
    out = entity("Y.IMG")
    expand_category_types("how many Image D records have lipidomic data", out, CATALOG)
    assert [i.code for i in out.sampletypes] == ["Y.IMG"]
    out = entity("Z.SEQ")
    expand_category_types("how many z.seq metagenome records", out, CATALOG)
    assert [i.code for i in out.sampletypes] == ["Z.SEQ"]


# R3: an -ing word names a process many kinds of data go through, not a category of data.
@pytest.mark.parametrize("query,start", [
    ("samples with imaging data", "Y.IMG"),
    ("any sequencing data for these", "Z.SEQ"),
])
def test_an_ing_word_is_not_a_category(query, start):
    out = entity("M.MUS", start)
    expand_category_types(query, out, CATALOG)
    assert {i.code for i in out.sampletypes} == {"M.MUS", start}
    assert out.category_types == {}


REAL_CATALOG = json.loads((Path(chat_nextseek.__file__).parent / "context" / "min_sampletypes_db.json").read_text())


@pytest.mark.parametrize("query,start", [
    ("how many mice have RNA sequencing", ["MUS", "D.SEQ"]),
    ("which tissues underwent sequencing", ["TIS", "D.SEQ"]),
    ("how many slides went through staining", ["SLD"]),
    ("which samples have imaging data", ["D.IMG"]),
])
def test_process_words_expand_nothing_on_the_real_catalog(query, start):
    out = entity(*start)
    expand_category_types(query, out, REAL_CATALOG)
    assert [i.code for i in out.sampletypes] == start
    assert out.category_types == {}


@pytest.mark.parametrize("query,start,word,expect", [
    ("how many mice have transcriptomic data", "A.GEX", "transcriptomic", {"A.GEX", "A.SCXP", "A.SPTX", "RNA"}),
    ("which samples have transcriptomics files", "RNA", "transcriptomics", {"A.GEX", "A.SCXP", "A.SPTX", "RNA"}),
])
def test_a_data_category_word_still_expands_on_the_real_catalog(query, start, word, expect):
    out = entity(start)
    expand_category_types(query, out, REAL_CATALOG)
    assert {i.code for i in out.sampletypes} == expect
    assert set(out.category_types[word]) == expect


# R2: the parser's echo of the entity result has no category_types slot, so the clamp restores it from the
# entity result and adds the category's codes, which is what the graph agent's projected plan carries.
@pytest.mark.parametrize("as_dict", [False, True])
@pytest.mark.parametrize("plan_cls", ["ParserPlan", "MultiParserPlan"])
def test_the_clamp_carries_category_types_to_the_graph_agent(as_dict, plan_cls):
    from chat_nextseek import orchestrator as orch
    from chat_nextseek.agents.graph import project_parser_plan
    from chat_nextseek.schemas import router
    cats = {"widgetomic": ["W.A", "W.B", "W.C"]}
    ent = EntityAgentOutput(sampletypes=[EntityItem(code=c, name=f"Type {c}") for c in ("MUS", "W.A", "W.B", "W.C")],
                            category_types=cats)
    plan = getattr(router, plan_cls)(resolved=EntityAgentOutput(sampletypes=[EntityItem(code="MUS"),
                                                                             EntityItem(code="W.A")]))
    out = orch._clamp_lab_codes_to_entity(plan, ent.model_dump() if as_dict else ent)
    resolved = project_parser_plan(out.model_dump())["resolved"]
    assert resolved["category_types"] == cats
    assert [t["code"] for t in resolved["sampletypes"]] == ["MUS", "W.A", "W.B", "W.C"]
    assert resolved["sampletypes"][2]["name"] == "Type W.B"


def test_the_clamp_adds_no_category_the_entity_step_did_not_find():
    from chat_nextseek import orchestrator as orch
    from chat_nextseek.schemas.router import ParserPlan
    plan = ParserPlan(resolved=EntityAgentOutput(sampletypes=[EntityItem(code="MUS")]))
    out = orch._clamp_lab_codes_to_entity(plan, EntityAgentOutput(sampletypes=[EntityItem(code="MUS")]))
    assert out.resolved.category_types == {}
    assert [t.code for t in out.resolved.sampletypes] == ["MUS"]


def test_a_plain_noun_expands_nothing():
    out = entity("M.MUS")
    expand_category_types("how many mouse samples", out, CATALOG)
    assert [i.code for i in out.sampletypes] == ["M.MUS"]


def agg(**cols):
    return {"ok": True, "count": 1, "data": [dict(cols)]}


def test_zero_answer_beside_total_is_nothing():
    assert matched_nothing(agg(total_cc_mice=165, with_gene_expression=0)) is True
    assert matched_nothing(agg(total=500, matched=0)) is True


def test_nonzero_answer_is_something():
    assert matched_nothing(agg(total=0, matched=3)) is False
    assert matched_nothing(agg(total=500, matched=3)) is False


def test_multi_row_is_something():
    assert matched_nothing({"ok": True, "count": 2, "data": [{"total": 5, "n": 0}, {"total": 5, "n": 0}]}) is False


def test_total_column_value():
    assert total_column_value({"total_mice": 165, "n": 0}) == 165
    assert total_column_value({"n": 0}) is None


class _Inp:
    def __init__(self, q, rows, total=None, count=1):
        self.question, self.rows, self.total, self.count = q, rows, total, count


class _T:
    def __init__(self, q, rows):
        self.q, self.rows, self.inp = q, rows, _Inp(q, rows)

    def result_n(self):
        return self.inp.count


def test_premise_compares_against_the_total_not_the_row_count():
    t = _T("how many of these 1,206 mice have x", [{"total_mice": 1206, "with_x": 8}])
    assert _premise_count(t) is None
    t = _T("how many of these 800 samples have y", [{"all_samples": 800, "with_y": 0}])
    assert _premise_count(t) is None
    t = _T("how many of these 900 samples have y", [{"all_samples": 800, "with_y": 0}])
    assert _premise_count(t) is not None
