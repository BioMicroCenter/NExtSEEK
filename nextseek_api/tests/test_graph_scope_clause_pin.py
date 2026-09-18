"""
The graph agent's scope clause and graph_search's are the same text.

chat_nextseek never imports nextseek_api, so the prover keeps its own template; this pin renders it with
graph_search's names and requires byte equality with graph_search's clause (spec decision 2, section 3.2 S2).

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 3.2 and 11.1.
"""
from __future__ import annotations

from chat_nextseek.cypher_scope import SCOPE_CLAUSE_TEMPLATE
from nextseek_api.graph_search.query import _SCOPE_MATCH


def test_scope_clause_is_graph_search_clause():
    assert SCOPE_CLAUSE_TEMPLATE.format(element="p", var="s", param="projects") == _SCOPE_MATCH
