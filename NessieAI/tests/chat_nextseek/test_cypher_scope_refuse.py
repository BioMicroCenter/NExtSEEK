"""
The prover refuses what it cannot prove, with the codes of spec section 5.8.

One case per row of tables 5.4 and 5.5 (graph_scope/battery.py REFUSALS). Each expression-level construct is placed
again directly, inside a nested EXISTS and after a WITH alias chain (each must refuse with the same code), and inside a
block comment, a line comment, a string literal and a backticked name (each must be accepted: only the real ones
refuse). Every refusal is collected, every reason names a line and a column, and the prover never raises: an
internal error is itself a refusal.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 5.2 to 5.8 and 11.1.
"""
from __future__ import annotations

import re

import pytest

from chat_nextseek import cypher_scope
from chat_nextseek.cypher_scope import REFUSAL_CODES, Refused, Scoped, scope_cypher
from chat_nextseek.graph_scope import GraphScope

from NessieAI.tests.chat_nextseek.graph_scope.battery import REFUSALS, hidden_variants

CALLER = GraphScope.for_projects([1, 3], source="test")
ADMIN = GraphScope.admin("test")
HIDDEN = hidden_variants()


def test_refusal_codes_are_the_spec_list():
    assert REFUSAL_CODES == (
        "too_long", "too_deep", "lexer", "syntax", "internal", "reserved_name", "reserved_parameter", "query_prefix",
        "union", "call_subquery", "collect_subquery", "procedure", "fulltext_form", "pattern_expression",
        "inline_where", "label_not_allowed", "label_expression", "unlabelled_node", "unjoined_node",
        "relationship_type", "variable_length", "path_selector", "hidden_property", "dynamic_property",
        "whole_properties", "function_not_allowed",
    )


def test_the_table_uses_every_code_but_internal():
    used = {code for case in REFUSALS for code in case.codes}
    assert used == set(REFUSAL_CODES) - {"internal"}


@pytest.mark.parametrize("case", REFUSALS, ids=[c.id for c in REFUSALS])
def test_refusal(case):
    out = scope_cypher(case.cypher, case.params, CALLER)
    assert isinstance(out, Refused), out
    assert out.decision == "refused"
    assert out.codes == case.codes, out.reasons
    assert out.reasons
    for reason in out.reasons:
        assert re.search(r"line \d+, column \d+", reason), reason


@pytest.mark.parametrize("variant", HIDDEN, ids=[v[0] for v in HIDDEN])
def test_hidden_construct(variant):
    _, cypher, codes = variant
    out = scope_cypher(cypher, {}, CALLER)
    if codes:
        assert isinstance(out, Refused), (cypher, out)
        assert out.codes == codes, (cypher, out.reasons)
    else:
        assert isinstance(out, Scoped), (cypher, getattr(out, "reasons", None))
        assert out.injected == ("s: sample clause",)


def test_every_refusal_is_collected_not_the_first_only():
    out = scope_cypher(
        "MATCH (s:Sample)-->(x:Attribute) WHERE s.parent_titles IS NOT NULL RETURN properties(s) AS p", {}, CALLER)
    assert isinstance(out, Refused)
    assert set(out.codes) == {"relationship_type", "label_not_allowed", "hidden_property", "whole_properties"}
    assert len(out.reasons) >= 4


def test_reasons_name_the_line_and_column():
    out = scope_cypher("MATCH (s:Sample)\nWITH s\nMATCH (st:Study)\nRETURN s.id AS id", {}, CALLER)
    assert isinstance(out, Refused) and out.codes == ("unjoined_node",)
    assert any("line 3, column 7" in reason for reason in out.reasons), out.reasons


def test_reserved_parameter_is_refused_for_admin_too():
    out = scope_cypher("MATCH (s:Sample) RETURN s.id AS id", {"__scope_projects": [99]}, ADMIN)
    assert isinstance(out, Refused)
    assert out.codes == ("reserved_parameter",)


def test_admin_skips_everything_but_the_reserved_parameter():
    for case in REFUSALS:
        if case.codes == ("reserved_parameter",):
            continue
        out = scope_cypher(case.cypher, case.params, ADMIN)
        assert isinstance(out, Scoped), case.id
        assert out.cypher == case.cypher


def test_an_internal_error_is_a_refusal(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cypher_scope, "_lex", boom)
    out = scope_cypher("MATCH (s:Sample) RETURN s.id AS id", {}, CALLER)
    assert isinstance(out, Refused)
    assert out.codes == ("internal",)


@pytest.mark.parametrize("bad", [None, 12, b"MATCH (s:Sample) RETURN s.id AS id"])
def test_a_statement_that_is_not_text_is_refused(bad):
    out = scope_cypher(bad, {}, CALLER)  # type: ignore[arg-type]
    assert isinstance(out, Refused)


@pytest.mark.parametrize("bad", [["a"], "x=1", 5])
def test_parameters_that_are_not_a_map_are_refused(bad):
    for scope in (CALLER, ADMIN):
        out = scope_cypher("MATCH (s:Sample) RETURN s.id AS id", bad, scope)  # type: ignore[arg-type]
        assert isinstance(out, Refused)
        assert out.codes == ("internal",)


def test_a_scope_that_is_not_a_graph_scope_is_refused():
    out = scope_cypher("MATCH (s:Sample) RETURN s.id AS id", {}, {"is_admin": True})  # type: ignore[arg-type]
    assert isinstance(out, Refused)
    assert out.codes == ("internal",)


def test_empty_and_blank_statements_are_refused():
    for text in ("", "   ", "// only a comment", ";"):
        out = scope_cypher(text, {}, CALLER)
        assert isinstance(out, Refused), text
        assert out.codes == ("syntax",)
