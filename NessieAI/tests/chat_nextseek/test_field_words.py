"""Lane B (who-asked-2, R3): a word the question counts that names sample fields, not a type or a container.

"How many <word> does <project> have?" was read as a count of Studies because the graph step saw attribute lists only
for the types the entity step resolved, and a member cannot read the attribute catalog by query. Before the graph
step writes anything, the word is looked up among the attribute titles of every type (the catalog snapshot's guard,
which a member's snapshot keeps), and the matching fields go to it as one block. Entities and the word are invented.
"""
from types import MappingProxyType
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_catalog as gcat
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.helpers import field_words as fw
from chat_nextseek.schemas import GraphAgentPlan


def _row(code, name):
    return gcat.TypeIndexRow(title=code, label="T_" + code.replace(".", "_"), name=name, clade="Source",
                             sample_count=None, deprecated=False, attributes_with_values=2)


SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h", synced_at=None, has_usage=False,
    index=(_row("ZEB", "Zebra"), _row("QUO", "Quoll"), _row("A.ZEB", "Zebra Analysis"), _row("TIS", "Tissue")),
    guard=MappingProxyType({
        "T_ZEB": frozenset({"Stripe_Band", "Colour", "Tier", "Protocol", "Dataset", "Test_Result", "BackLeg_Height"}),
        "T_QUO": frozenset({"BaselineBand", "Shade"}),
        "T_A_ZEB": frozenset({"Tier_Label"}),
        "T_TIS": frozenset({"Organ", "Bandage"}),
    }),
)


def _fields(question, keywords, projects=()):
    return fw.fields_named(SNAPSHOT, question, {"resolved": {"keywords": list(keywords), "projects": list(projects)}},
                           {})


def test_a_counted_word_finds_its_fields_on_every_type_that_has_one():
    found = _fields("How many bands does Quill have?", ["Quill", "bands"], projects=["Quill"])
    assert found == [("QUO", "BaselineBand"), ("ZEB", "Stripe_Band")]  # never Bandage: a word part, not a substring


def test_a_sibling_word_on_other_types_and_an_analysis_type():
    found = _fields("number of tiers per project", [])
    assert found == [("A.ZEB", "Tier_Label"), ("ZEB", "Tier")]


@pytest.mark.parametrize("question,keywords", [
    ("which samples have a band", ["band"]),          # not a count
    ("How many quolls have a baseline band?", ["band"]),  # the field word is a filter, not what is counted
    ("tissues with a band count above 100", ["band"]),    # "count" names a field here, not a count
    ("How many studies does Quill have?", ["studies"]),  # a container
    ("How many zebras are there?", ["zebra"]),          # a sample type's name
    ("How many quill samples?", ["Quill"]),             # nothing holds the word
    # F1 (review 2026-11-19): ordinary questions whose counted phrase holds a field word that is not what is counted
    ("how many zebra band tissue samples are in the database?", []),       # the head noun is a type word
    ("How many protocols are registered for the Quill investigation?", []),  # a protocol is a record
    ("How many imaging datasets are in the Quill investigation?", []),     # so is a dataset
    ("How many results came back from that last search?", []),            # a result of the last search
    ("How many results did that return", []),
    ("How many big old stripe zebra bands live here?", []),                # no phrase end in reach: head unknown
])
def test_no_block_when_the_word_is_not_a_counted_field(question, keywords):
    assert _fields(question, keywords) == []


def test_the_counted_word_is_the_head_of_the_counted_phrase():
    """Only the phrase's last word is what is counted: "stripe bands" counts bands, "band tissues" counts tissues."""
    assert _fields("How many stripe colours are there for Quill?", [], projects=["Quill"]) == [("ZEB", "Colour")]
    assert _fields("How many band colours are there?", []) == [("ZEB", "Colour")]


def test_the_heading_asks_for_distinct_values_only_when_the_question_counts_them():
    assert "when the question counts that field's values" in fw.FIELDS_HEADING
    assert "such a word is a field of the samples" not in fw.FIELDS_HEADING


def test_the_block_names_type_and_title_and_a_count_only_when_given():
    block = fw.render_fields([("QUO", "BaselineBand"), ("ZEB", "Stripe_Band")], {("ZEB", "Stripe_Band"): 12})
    assert block.startswith(fw.FIELDS_HEADING)
    assert "- QUO: `BaselineBand`\n" in block + "\n"
    assert "- ZEB: `Stripe_Band` (12 samples)" in block
    assert fw.render_fields([], {}) == ""


def _llm(calls):
    def call(**kwargs):
        calls.append(kwargs)
        return GraphAgentPlan(cypher="MATCH (z:T_ZEB) RETURN count(DISTINCT z.Stripe_Band) AS n", parameters={})
    return call


def _config(scope):
    c = MagicMock()
    c.GRAPH_SCOPE = scope
    c.NEO4J_SCHEMA = {}
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


@pytest.mark.parametrize("scope,count", [(GraphScope.for_projects([7], source="test"), None),
                                         (GraphScope.admin("test"), 12)])
def test_the_graph_step_is_handed_the_fields_before_it_writes_for_a_member_too(monkeypatch, scope, count):
    calls = []
    detail = gcat.TypeDetail(title="ZEB", label="T_ZEB", name="Zebra", summary=None, clade="Source", sample_count=None,
                             curated_parents=None, curated_children=None, never_filled=0, attributes=(
                                 gcat.AttributeRow(title="Stripe_Band", value_type="string", declared=True,
                                                   needs_backticks=False, sample_count=count, meaning=None,
                                                   unit_key=None, role="data"),))
    monkeypatch.setattr(gcat, "get_snapshot", lambda config: SNAPSHOT)
    monkeypatch.setattr(gcat, "get_type_details", lambda config, titles: [detail] if "ZEB" in titles else [])
    monkeypatch.setattr(gcat, "get_vocabulary", lambda config: gcat.Vocabulary((), ("Quill",), (), (), (), (), ()))
    monkeypatch.setattr(graph_mod, "call_llm_structured", _llm(calls))
    plan = {"mode": "graph_query", "resolved": {"keywords": ["Quill", "bands"], "projects": ["Quill"]}}

    graph_mod.graph_agent(_config(scope), "How many bands does Quill have?", {}, plan)

    blob = "\n".join(m["content"] for m in calls[0]["messages"])
    assert fw.FIELDS_HEADING in blob
    assert "- QUO: `BaselineBand`" in blob
    assert ("- ZEB: `Stripe_Band` (12 samples)" if count else "- ZEB: `Stripe_Band`\n") in blob + "\n"


def test_a_two_word_counted_phrase_skips_its_container_word():
    assert _fields("How many study bands are there for Quill?", [], projects=["Quill"]) == [
        ("QUO", "BaselineBand"), ("ZEB", "Stripe_Band")]


def test_a_failing_lookup_costs_the_block_not_the_catalog(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("bad snapshot")
    monkeypatch.setattr(fw, "fields_named", broken)
    assert graph_mod._fields_block(MagicMock(), SNAPSHOT, "How many bands?", {}, {}) == ""
