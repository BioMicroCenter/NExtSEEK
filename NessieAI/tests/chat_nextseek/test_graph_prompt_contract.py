"""What the graph agent prompt promises the graph-result reviewer.

The reviewer reads each graph turn's returned rows to tell the user which values a fuzzy filter really
matched. It can see a value split only when the filtered attribute comes back as a column, so every graph
prompt in use carries that rule in its RETURN rules (plan task 6, B4). The v2_apoc addendum is appended to
``graph_agent.txt`` and inherits its rules, so the base file is the whole set.
"""
from __future__ import annotations

import pathlib

import chat_nextseek

P = pathlib.Path(chat_nextseek.__file__).resolve().parent / "prompts"


def _graph_prompt() -> str:
    return (P / "graph_agent.txt").read_text(encoding="utf-8")


def test_the_graph_prompt_set_is_the_base_file():
    # Guards the loop below against passing on an empty glob.
    assert [f.name for f in P.glob("graph_agent*.txt")] == ["graph_agent.txt"]


def test_graph_prompts_carry_the_return_contract():
    for f in P.glob("graph_agent*.txt"):
        t = f.read_text()
        assert "Return every attribute you filter on as its own column" in t, f.name


def test_the_return_contract_sits_in_the_list_rule_of_the_return_rules():
    t = _graph_prompt()
    rules = t[t.index("## STEP 6: Return the answer"):t.index("## When a query matched nothing")]
    list_rule = rules[rules.index("- **A list of samples**"):]
    list_rule = list_rule[:list_rule.index("\n- ")]
    assert ("Return every attribute you filter on as its own column, next to the id, so the reader can see which "
            "values matched (for example `s.Classification AS Classification` when you filter on Classification)."
            ) in list_rule


# --- SCH-F8: an edge several assays share lists all of them in internal_assay_titles ----------------------------------
# 3,645 DERIVED_FROM edges name more than one assay, and 5 assay titles exist only in the plural list, so a test on
# the singular alone misses them.

ASSAY_TEST = "(r.internal_assay_title = $assay OR $assay IN coalesce(r.internal_assay_titles, []))"
FUZZY_ASSAY_TEST = ("(toLower(r.internal_assay_title) CONTAINS toLower($term) "
                    "OR any(t IN coalesce(r.internal_assay_titles, []) WHERE toLower(t) CONTAINS toLower($term)))")


def test_every_assay_example_tests_the_singular_and_the_plural():
    t = _graph_prompt()
    assert t.count("WHERE " + ASSAY_TEST) == 2
    assert "WHERE r.internal_assay_title = $assay\n" not in t
    assert "r.internal_assay_title = $assay" not in t.replace(ASSAY_TEST, "")


def test_the_fuzzy_assay_fallback_reads_the_plural_too():
    t = _graph_prompt()
    assert "use `" + FUZZY_ASSAY_TEST + "`" in t
    assert "toLower(r.internal_assay_title) CONTAINS toLower($term)" not in t.replace(FUZZY_ASSAY_TEST, "")


def test_the_schema_section_names_the_plural_edge_property():
    t = _graph_prompt()
    rel = t[t.index("- **Relationships**"):]
    rel = rel[:rel.index("\n- ")]
    assert "`internal_assay_title`, `internal_assay_titles`, `protocol_title`" in rel
    assert "an edge several assays share lists all of them in `internal_assay_titles`, which some edges lack" in rel


def test_the_variable_length_typing_rule_is_unchanged():
    assert ("`[r:DERIVED_FROM*1..12]` binds a list, so `r.internal_assay_title` there is a type error."
            in _graph_prompt())
