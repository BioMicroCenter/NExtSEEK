"""graph_agent.txt: "samples that have <kind> data" returns the samples, and a person reached through lineage
returns their own records beside the lineage count (round 2, P4)."""
from __future__ import annotations

import re
from pathlib import Path

import chat_nextseek
from chat_nextseek.agents.graph import query_shape_problems
from chat_nextseek.cypher_scope import Scoped, scope_cypher
from chat_nextseek.graph_scope import GraphScope

PROMPT = (Path(chat_nextseek.__file__).resolve().parent / "prompts" / "graph_agent.txt").read_text(encoding="utf-8")
MEMBER = GraphScope.for_projects([1], source="test")


def _line(start: str) -> str:
    return next(l for l in PROMPT.splitlines() if l.lstrip().startswith(start))


def _statement(line: str, pattern: str) -> str:
    return re.search(pattern, line).group(1)


def _both_ways(cypher: str, params: dict) -> None:
    assert query_shape_problems(cypher, params, scoped=True) == []
    out = scope_cypher(cypher, params, MEMBER)
    assert isinstance(out, Scoped), getattr(out, "reasons", out)


def test_the_prompt_returns_samples_not_records_for_a_samples_with_data_question():
    line = _line("- **Data records or the samples they came from?**")
    assert "count the records of every data type of that kind" not in line
    cy = _statement(line, r"`(MATCH \(d:Sample\)-\[:IN_PROJECT\].*?LIMIT 5000)`")
    assert "NOT s.type IN $kinds" in cy
    _both_ways(cy, {"kinds": ["D.ZZZ"], "project_title": "P"})


def test_the_prompt_still_lists_records_when_the_data_itself_is_asked():
    line = _line("- **Data records or the samples they came from?**")
    assert "Ask for the records only when the question asks for the data, files or records themselves" in line


def test_a_person_reached_through_lineage_returns_their_own_records():
    line = _line("- A name in `resolved.scientists` is a scientist for the whole question")
    assert "count(DISTINCT m) AS made" in line and "collect(DISTINCT m.type) AS made_types" in line
    cy = _statement(line, r"`(MATCH \(s:T_PAT\).*?AS made_types)`")
    _both_ways(cy, {"project": "P", "person": "Ann"})
    # the Scientist CONTAINS rule one line up is unchanged
    assert "`toLower(toString(s.Scientist)) CONTAINS toLower($person)`" in _line("- anyone else (the question says who made")
