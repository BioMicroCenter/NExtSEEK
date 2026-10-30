"""A question about data existing, scoped to an investigation whose catalog description says
it holds no D.* record of that kind, gets the type the description names in its place (#143).

The rule reads the PROJECTS CATALOG descriptions, so it names no investigation itself: on
2026-10-30 its example naming one was removed (house rule 85b25b8b: a corpus or user case is
never a prompt example), and the graded corpus cases graph.tcga_*_with_sequencing and
graph.impact_monkeys_with_sequencing_stays_dseq prove it. The prompt is read by path from this
checkout, as in test_variant_offer_prompts.py.
"""
import json
import re
from pathlib import Path

CHAT_NEXTSEEK = Path(__file__).resolve().parents[2] / "chat_nextseek" / "src" / "chat_nextseek"
PROMPTS = CHAT_NEXTSEEK / "prompts"
PROJECTS = CHAT_NEXTSEEK / "context" / "projects_db.json"


def _entity() -> str:
    return (PROMPTS / "entity_agent.txt").read_text(encoding="utf-8")


def _rule(text: str) -> str:
    start = text.index("One exception: when the question is scoped to an investigation")
    return text[start:text.index("A question asking which variants or mutations", start)]


def test_the_scoped_data_type_exception_is_in_the_data_object_rule():
    text = _entity()
    rule = _rule(text)
    assert text.index("DATA OBJECT RULE") < text.index(rule)
    assert "whose PROJECTS CATALOG\ndescription says it holds no D.* record of that kind" in rule
    assert "emit the type the description names\ninstead." in rule


def test_the_final_self_check_allows_the_named_type():
    assert ("did I emit the D.* sampletype,\n"
            "   or the type a scoped investigation's description names in its place?") in _entity()


def test_the_rule_names_no_investigation():
    """Every catalog name and alternative name, not only the one the rule was written for."""
    rule = _rule(_entity())
    rows = json.loads(PROJECTS.read_text(encoding="utf-8"))
    names = {n for r in rows for n in [r.get("name"), *(r.get("alternative_names") or [])] if n}
    assert "TCGA" in names  # the row the rule was written for is still in the catalog
    for name in sorted(names):
        assert not re.search(rf"\b{re.escape(name)}\b", rule, flags=re.IGNORECASE), name
    for code in ("A.ALN", "D.SEQ", "erlotinib"):
        assert code not in rule, code
