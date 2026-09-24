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
