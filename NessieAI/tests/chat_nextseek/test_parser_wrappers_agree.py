"""The two parser wrappers share a routing core, so they must not contradict it (F1 fallout).

`parser_agent.txt` was promoted from the measured set and gained the lab fields. The multi-step
wrapper shares the same routing core (both inject `{{PARSER_CORE_ROUTING}}`) but was not promoted
with it, and drifted in two ways an adversarial review of the branch caught:

* its output schema had no `lab_codes` anywhere, while the single-plan wrapper and the schema the
  code validates against both carry them;
* its "comparison post-filters" rule told the model to reduce a numeric or date comparison to a
  keyword anchor on the field name, which the promoted core's step 8 forbids in as many words.

A guard for the first class existed and was deleted with the variant directories it compared to
(`test_the_parser_wrappers_are_not_overridden`). This replaces it with one that compares the
wrappers to each other and to the core, which is the relationship that actually matters.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PROMPTS = Path(__file__).resolve().parents[2] / "chat_nextseek" / "src" / "chat_nextseek" / "prompts"
CORE = (PROMPTS / "parser_core_routing.txt").read_text(encoding="utf-8")
SINGLE = (PROMPTS / "parser_agent.txt").read_text(encoding="utf-8")
MULTI = (PROMPTS / "multi_parser_agent.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("wrapper,name", [(SINGLE, "parser_agent"), (MULTI, "multi_parser_agent")])
def test_both_wrappers_inject_the_same_routing_core(wrapper, name):
    assert wrapper.count("{{PARSER_CORE_ROUTING}}") == 1, name


@pytest.mark.parametrize("wrapper,name", [(SINGLE, "parser_agent"), (MULTI, "multi_parser_agent")])
def test_both_wrappers_carry_the_lab_fields(wrapper, name):
    """The graph agent reads resolved lab codes; a wrapper that cannot emit them starves it."""
    assert '"lab_codes"' in wrapper, name
    assert '"labs"' in wrapper, name


def test_no_wrapper_contradicts_the_core_on_comparisons():
    """The core: a numeric or date comparison runs inside the graph query, never as a keyword."""
    flat_core = " ".join(CORE.split())
    assert "Never reduce the comparison to a keyword search on the field name" in flat_core

    flat_multi = " ".join(MULTI.split())
    assert "A numeric or date comparison belongs in graph_query" in flat_multi
    assert "Never reduce a comparison to a keyword search on the field name" in flat_multi
    # The keyword-anchor fallback survives, but only for a candidate that is REST for some
    # other reason -- it may no longer be the advice for a comparison as such.
    assert "Only when candidate 0 is new_search for some OTHER reason" in flat_multi
