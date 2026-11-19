"""Lane B (who-asked-2, R1): the graph prompt stops offering the sample properties the scope check hides.

``parent_titles`` was listed as an ordinary system property of every sample, so "every stored property" invited a
refusal for any member. The line is generated from ``graph_scope.HIDDEN_SAMPLE_PROPERTIES``, the check's own list:
an admin, who may read them, sees them listed; anyone else is told never to read them.
"""
from pathlib import Path

from chat_nextseek import graph_scope
from chat_nextseek.agents import graph as graph_mod

PROMPT = (Path(graph_mod.__file__).resolve().parents[1] / "prompts" / "graph_agent.txt").read_text(encoding="utf-8")


def _line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("- **System properties**"))


def test_the_shipped_prompt_names_no_hidden_property_by_hand():
    for name in graph_scope.HIDDEN_SAMPLE_PROPERTIES:
        assert name not in PROMPT, name
    assert graph_mod.HIDDEN_PROPERTIES_SLOT in _line(PROMPT)


def test_a_member_is_told_never_to_read_each_hidden_property():
    line = _line(graph_mod.render_hidden_properties(PROMPT, scoped=True))
    assert graph_mod.HIDDEN_PROPERTIES_SLOT not in line
    for name in graph_scope.HIDDEN_SAMPLE_PROPERTIES:
        assert f"`{name}`" in line
    assert "never read" in line.lower() and "refused" in line


def test_an_admin_still_sees_them_listed():
    line = _line(graph_mod.render_hidden_properties(PROMPT, scoped=False))
    for name in graph_scope.HIDDEN_SAMPLE_PROPERTIES:
        assert f"`{name}`" in line
    assert "never read" not in line.lower()


def test_the_line_follows_the_checks_list_not_a_copy(monkeypatch):
    monkeypatch.setattr(graph_mod, "HIDDEN_SAMPLE_PROPERTIES", frozenset({"zz_shadow"}))
    line = _line(graph_mod.render_hidden_properties(PROMPT, scoped=True))
    assert "`zz_shadow`" in line and "parent_titles" not in line
