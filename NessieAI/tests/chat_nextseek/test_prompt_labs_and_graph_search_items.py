"""The prompt items owed after the labs, 7.1 and Sample Search merges (graph agent handoff, 2026-09-18).

Each test pins one item, read by path from this checkout so an installed copy of the package cannot mask it:

* Lab codes come only from SEEK's lab records (spec 2026-09-18-projects-labs-context.md, sections 7.8 and 13.2), so no
  parser core may teach deriving one from a name: the old "Kamm -> KAM" example is gone.
* TCGA joins every hand-written investigation list with the generated block's "not on every instance" note, and the
  lists name exactly the investigations the generated capabilities block names (13.2, 13.3).
* The entity agent emits people in `labs` or `scientists`, and reads a projects row as an investigation only when it
  is typed investigation AND names a parent project (13.1); the system agent is told the same (13.5); the graph agents
  read `resolved.scientists` and `resolved.lab_matches` (13.4).
* 7.1 falls back to graph_search for a refused graph question on every engine, and both engines' fallbacks build the
  request from the DEFAULT config. The default catalog and API prompt must describe graph_search as it is now:
  NOT CONTAINS, IS TRUE, IS FALSE, lineage in either direction, 1 to 12 hops, results limited to the caller's projects.
* 6.14: no catalog sends a question about a person who made samples to /people/.
"""

import json
import re
from pathlib import Path

import pytest

NESSIE = Path(__file__).resolve().parents[2]
PACKAGE = NESSIE / "chat_nextseek" / "src" / "chat_nextseek"
PROMPTS = PACKAGE / "prompts"
CONTEXT = PACKAGE / "context"
CORES = {
    "default": PROMPTS / "parser_core_routing.txt",
}
GRAPH_SCHEMAS = {"default": CONTEXT / "min_graph_schema.json",
}
CATALOGS = {"default": CONTEXT / "min_api_endpoints_enriched.json",
}
GRAPH_AGENTS = {"default": PROMPTS / "graph_agent.txt"}
GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- lab codes


@pytest.mark.parametrize("name", sorted(CORES))
def test_no_parser_core_derives_a_lab_code_from_a_name(name):
    text = read(CORES[name])
    assert "Kamm -> KAM" not in text and "e.g. KAM" not in text
    rule = next(line for line in text.splitlines() if line.startswith("For a lab/PI-scoped query"))
    assert "exactly as given" in rule
    assert "Never derive a code from a name" in rule or "never deriving a code from a name" in rule


def test_the_api_prompts_use_invented_lab_codes():
    for path in (PROMPTS / "api_agent.txt",):
        text = read(path)
        assert '"KAM"' not in text and '"SHA"' not in text, path


# --------------------------------------------------------------------------- investigations


def _generated_investigations() -> list[str]:
    caps = read(CONTEXT / "capabilities.md")
    block = caps[caps.index("<!-- BEGIN CONTEXT-GEN:investigations -->"):caps.index("<!-- END CONTEXT-GEN:investigations -->")]
    return re.findall(r"^- \*\*(.+?)\*\*", block, re.M)


@pytest.mark.parametrize("name", sorted(CORES))
def test_each_core_lists_the_generated_investigations_and_marks_tcga(name):
    line = next(l for l in read(CORES[name]).splitlines() if l.startswith("Known investigation titles:"))
    for investigation in _generated_investigations():
        assert investigation in line, (name, investigation)
    assert "TCGA, which is not on every instance" in line


@pytest.mark.parametrize("name", sorted(GRAPH_SCHEMAS))
def test_each_parser_graph_schema_lists_tcga_as_not_on_every_instance(name):
    schema = json.loads(read(GRAPH_SCHEMAS[name]))
    investigation = next(n for n in schema["node_types"] if n["label"] == "Investigation")
    assert "TCGA, which is not on every instance" in investigation["description"]
    rule = next(r for r in schema["disambiguation_rules"] if r.startswith("Investigation titles to recognize"))
    for name_ in _generated_investigations():
        assert name_ in rule


# --------------------------------------------------------------------------- people: labs and scientists


def test_the_entity_agent_emits_scientists_and_reads_investigation_rows_by_both_fields():
    text = read(PROMPTS / "entity_agent.txt")
    assert '"scientists": [' in text
    assert "SCIENTISTS:" in text
    assert "is an investigation only when its `entity_type` is" in text and "`parent_project`" in text
    assert "never read `entity_type`\nalone" in text
    for real in ("Kamm", "Shalek"):
        assert real not in text


def test_the_system_agent_knows_which_entity_details_are_investigations():
    text = read(PROMPTS / "system_agent.txt")
    assert '"<name> (investigation)"' in text and "names a parent project" in text
    assert "Study rows are not sent" in text


@pytest.mark.parametrize("name", sorted(GRAPH_AGENTS))
def test_the_graph_agents_read_scientists_and_lab_matches(name):
    text = read(GRAPH_AGENTS[name])
    assert "`resolved.scientists`" in text and "`resolved.lab_matches`" in text
    assert "not on every instance (TCGA)" in text


# --------------------------------------------------------------------------- graph_search as it is now


def _entry(catalog: Path, path: str) -> dict | None:
    return next((e for e in json.loads(read(catalog)) if e.get("path") == path), None)


@pytest.mark.parametrize("name", sorted(CATALOGS))
def test_every_catalog_describes_graph_search_as_it_is_now(name):
    entry = _entry(CATALOGS[name], GRAPH_SEARCH)
    assert entry is not None, f"the {name} catalog has no graph_search entry: 7.1's fallback builds blind"
    desc = entry["description"]
    for phrase in ("NOT CONTAINS", "IS TRUE", "IS FALSE", "either", "1 to 12 hops", "limited to the caller's projects"):
        assert phrase in desc, (name, phrase)
    assert "1 to 4" not in desc
    assert entry["request_body"]["extensions"]["lineage"]["max_hops"] == 12


def test_the_default_api_prompt_can_build_a_graph_search_body():
    text = read(PROMPTS / "api_agent.txt")
    assert "graph_search: the predicates advanced_search cannot express" in text
    assert '"max_hops": 1-12' in text and "NOT CONTAINS" in text and '"either"' in text
    assert "could not be confirmed to stay within the caller's projects" in text
    assert '- "filter_searchText" is ONE string.' in text


# --------------------------------------------------------------------------- 6.14


@pytest.mark.parametrize("name", sorted(CATALOGS))
def test_no_catalog_sends_a_sample_maker_to_the_people_endpoint(name):
    entry = _entry(CATALOGS[name], "/nextseek_api/people/")
    assert entry is not None
    assert not any("scientist" in p.lower() or p.lower() == "who" for p in entry["intent_patterns"])
    assert not any("scientist" in x.lower() for x in entry["example_intents"])
    assert "never" in entry["llm_hint"].lower()
