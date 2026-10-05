"""Round 5 graph prompt wording: G4 (cheap walk, no pair lists) and G5 (empty means the node, not the title).

Each test pins one behaviour of the prompt text, and any example the prompt teaches must itself pass the shape
guard, so a prompt can never teach a query the guard refuses.
"""
from __future__ import annotations

import re
from pathlib import Path

from chat_nextseek.agents import graph as graph_mod

PROMPTS = Path(__file__).resolve().parents[2] / "chat_nextseek" / "src" / "chat_nextseek" / "prompts"
GRAPH_AGENT = (PROMPTS / "graph_agent.txt").read_text()
ASSAYS = " ".join((PROMPTS / "graph_schema_structure_assays.txt").read_text().split())


def _fenced_after(text: str, marker: str) -> str:
    tail = text[text.index(marker):]
    return re.search(r"```\n(.*?)```", tail, re.S).group(1)


def test_the_descendant_assay_example_starts_from_the_edges_that_carry_the_assay():
    assert "start from the edges that carry it" in GRAPH_AGENT
    example = _fenced_after(GRAPH_AGENT, "with a descendant that underwent the assay")
    assert example.lstrip().startswith("MATCH (c:Sample)-[r:DERIVED_FROM]->(:Sample)")
    assert "WITH DISTINCT c" in example and "count(DISTINCT m)" in example
    assert "EXISTS" not in example
    assert graph_mod.refused_query_shapes(example) == []


def test_the_old_walk_down_from_every_sample_of_the_type_is_gone():
    assert "MATCH (m)<-[:DERIVED_FROM*1..12]-(c:Sample)" not in GRAPH_AGENT


def test_with_no_uid_the_prompts_say_group_by_assay_and_count():
    for text in (ASSAYS,):
        assert "count(DISTINCT s) AS samples ORDER BY samples DESC LIMIT 50" in text
        assert "never list pairs" in text
        assert "one UID gives its partners" in text


def test_the_assay_join_repair_line_says_group_by_assay_and_count():
    shapes = graph_mod.query_shape_problems(
        "MATCH (s:Sample)-[:INPUT_TO|OUTPUT_OF]->(a:Assay)<-[:INPUT_TO|OUTPUT_OF]-(o:Sample) RETURN s, o")
    lines = " ".join(graph_mod._shape_lines(shapes))
    assert "cannot be paired through an Assay" not in lines
    assert "never list pairs" in lines and "count(DISTINCT s) AS samples" in lines
