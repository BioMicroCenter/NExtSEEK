"""The shipped prompt and context files: they keep every placeholder the code fills, and they agree with the code
that runs their output.

Every file is read by path from this checkout, never through ``chat_nextseek.__file__``, so the test checks the tree
it sits in even when an installed copy of the package is first on the path. The guard imports only supply the
property sets and the pure guard functions the prompts must satisfy.

These rules were developed as prompt variant v2 and promoted to the defaults by F1, so the guards read prompts/ and
context/ by path. What the promotion changed: every sample-metadata question routes to graph_query. The parser's
other modes (system_question, reporter, follow-ups, pipeline, unsupported) were deliberately left alone, and the
tests that pinned them against a default copy went with the variant directory they compared to.
"""

import json
import re
from pathlib import Path
from types import MappingProxyType

import pytest

from chat_nextseek import cypher_text
from chat_nextseek import graph_catalog as gcat
from chat_nextseek.agents.graph import (
    V11_NODE_PROPERTIES,
    V11_RELATIONSHIP_PROPERTIES,
    V12_SYSTEM_PROPERTIES,
    catalog_unknown_properties,
    whole_node_returns,
)
from chat_nextseek.helpers.tools.nextseek_api import _is_read_only_request

NESSIE = Path(__file__).resolve().parents[2]
REPO_ROOT = NESSIE.parent
PACKAGE = NESSIE / "chat_nextseek" / "src" / "chat_nextseek"
PROMPTS = PACKAGE / "prompts"
CONTEXT = PACKAGE / "context"

# The loader's contract: the only files a variant may carry, and where each default lives.
DEFAULTS = {
    "graph_agent.txt": PROMPTS / "graph_agent.txt",
    "graph_schema_structure.txt": PROMPTS / "graph_schema_structure.txt",
    "parser_core_routing.txt": PROMPTS / "parser_core_routing.txt",
    "parser_agent.txt": PROMPTS / "parser_agent.txt",
    "multi_parser_agent.txt": PROMPTS / "multi_parser_agent.txt",
    "api_agent.txt": PROMPTS / "api_agent.txt",
    "min_graph_schema.json": CONTEXT / "min_graph_schema.json",
    "min_api_endpoints_enriched.json": CONTEXT / "min_api_endpoints_enriched.json",
}
PLACEHOLDER = "{{PARSER_CORE_ROUTING}}"
GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def shipped(name: str) -> str:
    """A prompt or context file as it ships, by path from this checkout."""
    return read(DEFAULTS[name])


# --- the files are well formed ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("wrapper", ["parser_agent.txt", "multi_parser_agent.txt"])
def test_the_wrappers_compose_with_the_shipped_routing_core(wrapper):
    default = read(DEFAULTS[wrapper])
    assert default.count(PLACEHOLDER) == 1
    core = shipped("parser_core_routing.txt")
    composed = default.replace(PLACEHOLDER, core)
    assert PLACEHOLDER not in composed
    assert "{{" not in composed and "}}" not in composed
    assert core in composed


@pytest.mark.parametrize("name", ["graph_agent.txt", "graph_schema_structure.txt", "parser_core_routing.txt",
                                  "api_agent.txt"])
def test_no_text_override_carries_a_template_marker(name):
    # Only the two parser wrappers are templated ({{NAME}} markers), and v2 overrides neither. JSON braces are fine.
    assert not re.search(r"\{\{[A-Z_]+\}\}", shipped(name))


def test_the_context_json_parses_and_carries_graph_search():
    schema = json.loads(shipped("min_graph_schema.json"))
    assert isinstance(schema, dict) and schema

    endpoints = json.loads(shipped("min_api_endpoints_enriched.json"))
    assert isinstance(endpoints, list) and endpoints
    assert all(isinstance(e, dict) and e.get("path") and e.get("method") for e in endpoints)
    # 7.1 falls back to graph_search from the shipped prompts, so the catalog must advertise it.
    assert GRAPH_SEARCH in {e["path"] for e in endpoints}


# --- the routing core sends metadata to the graph -----------------------------------------------------------------------------------


def _section(text: str, header: str) -> str:
    """One PATH section of the routing core, up to the next PATH or the HOW TO CHOOSE heading."""
    start = text.index(f"PATH: {header}\n")
    ends = [i for i in (text.find("\nPATH: ", start + 1), text.find("\nHOW TO CHOOSE", start + 1)) if i != -1]
    return text[start:min(ends)]


def test_the_routing_core_sends_sample_metadata_to_the_graph():
    core = shipped("parser_core_routing.txt")
    assert "Use graph_query for every question about sample metadata" in core
    # The default's rules that sent metadata questions to REST are gone.
    for rest_rule in ("route to graph_query ONLY when answering requires a relationship hop",
                      "use new_search and copy ENTITY_RESULT.lab_codes",
                      "If the query can be fully answered by filtering on sample record fields, use new_search",
                      "Use new_search for candidate retrieval",
                      "ONE UID is REST work",
                      "Can a REST endpoint fully satisfy the intent using only sample attribute filters?"):
        assert rest_rule not in core, rest_rule
    assert "Never use /people/ for this" in core


def test_the_parser_graph_view_is_schema_1_2_and_no_rule_sends_metadata_to_rest():
    schema = json.loads(shipped("min_graph_schema.json"))
    sample = next(n for n in schema["node_types"] if n["label"] == "Sample")["description"]
    for claim in ("1.2", "search_text", "typed", "lab"):
        assert claim in sample, claim
    assert {"IN_PROJECT"} <= {r["type"] for r in schema["relationships"]}
    for rule in schema["disambiguation_rules"] + schema["api_preferred_triggers"]:
        assert "advanced_search endpoint" not in rule and "prefer API" not in rule, rule
        assert not re.search(r"→ API\b", rule), rule
    # F3 depends on this: the parser's graph view must know Project, or an organizational name it
    # does not recognise has no node to match against and the ladder refuses the question.
    assert "Project" in {n["label"] for n in schema["node_types"]}


# --- the graph agent's prompt agrees with the guards that check its output -------------------------------------------


GRAPH_FILES = ("graph_agent.txt", "graph_schema_structure.txt")


def _fenced(text: str) -> list[str]:
    return [block.strip() for block in re.findall(r"```\n(.*?)```", text, re.S)]


def _row(title):
    label = "T_" + re.sub(r"[^A-Za-z0-9_]", "_", title)
    return gcat.TypeIndexRow(title=title, label=label, name=None, clade=None, sample_count=10, deprecated=False,
                             attributes_with_values=1)


# The attributes the examples use, each checked on the live 1.2 graph when the prompt was written.
SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h", synced_at=None, has_usage=False,
    index=tuple(_row(t) for t in ("SLD", "MUS", "CHM", "D.IMG", "WTR", "AB")),
    guard=MappingProxyType({
        "T_SLD": frozenset({"Stain", "PercentNecrosis", "Scientist"}),
        "T_MUS": frozenset({"Treatment1", "Treatment2", "Treatment3", "Scientist"}),
        "T_CHM": frozenset({"Vendor", "Concentration", "Scientist"}),
        "T_D_IMG": frozenset({"Scientist"}),
        "T_WTR": frozenset({"Dechlorinated", "CollectionDate", "Scientist"}),
        "T_AB": frozenset({"Analyte", "Scientist"}),
    }),
)


# The worked-example guards (write check, catalog guard, bounded paths) live in
# test_default_prompt_set.py, whose catalog snapshot covers the examples the promoted
# prompt actually uses. Duplicating them here with an older snapshot only rots.


def test_the_bound_is_the_measured_longest_chain_plus_one():
    # Measured on the 1.2 graph: chains of 11 hops exist, none of 12, so *1..12 reaches every ancestor.
    agent = shipped("graph_agent.txt")
    assert "11 hops" in agent and "[:DERIVED_FROM*1..12]" in agent
    assert "11 hops" in shipped("graph_schema_structure.txt")


def test_the_system_properties_the_prompt_names_are_the_guard_s():
    agent = shipped("graph_agent.txt")
    line = next(l for l in agent.splitlines() if l.startswith("- **System properties**"))
    named = set(re.findall(r"`([a-z_]+)`", line.split(".", 1)[0] + line))
    assert V12_SYSTEM_PROPERTIES <= named
    structure = shipped("graph_schema_structure.txt")
    sample = structure[structure.index("(:Sample:T_<code>)"):structure.index("(:SampleType")]
    for prop in V12_SYSTEM_PROPERTIES:
        assert re.search(rf"\b{prop}\b", sample), prop


def test_every_property_the_structure_lists_on_another_label_is_allowed_by_the_guard():
    structure = shipped("graph_schema_structure.txt")
    for label, props in re.findall(r"\(:([A-Za-z]+) \{([^}]*)\}", structure):
        names = {p.strip() for p in props.replace("\n", " ").split(",") if p.strip()}
        allowed = V11_NODE_PROPERTIES.get(label) or V11_RELATIONSHIP_PROPERTIES.get(label)
        assert allowed is not None, label
        assert names <= allowed, (label, names - allowed)
    for rel, props in re.findall(r"\[:([A-Z_]+) \{([^}]*)\}\]", structure):
        names = {p.strip() for p in props.split(",")}
        assert names <= V11_RELATIONSHIP_PROPERTIES[rel], (rel, names - V11_RELATIONSHIP_PROPERTIES[rel])


def test_list_properties_are_carved_out_of_the_to_string_rule():
    agent = shipped("graph_agent.txt")
    rule = agent[agent.index("**Never compare free text with a bare `=`.**"):]
    rule = rule[:rule.index("\n")]
    for prop in ("project_ids", "parent_titles", "parent_title_hashes"):
        assert prop in rule


def test_the_lab_code_is_matched_after_the_date_not_at_the_start():
    agent = shipped("graph_agent.txt")
    assert "<TYPE>-<YYMMDD><LAB>-<n>" in agent
    assert "s.uuid =~ ('(?i)^[^-]+-[0-9]{6}' + $lab + '-.*')" in agent
    assert "never at the start" in agent


def test_person_names_never_go_to_person_nodes_or_the_people_endpoint():
    agent = shipped("graph_agent.txt")
    assert "`Scientist` attribute" in agent and "Never use `Person` nodes for a name" in agent
    api = shipped("api_agent.txt")
    assert "NEVER call /nextseek_api/people/ to find samples by a person" in api


def test_the_prompt_never_promises_a_values_list_the_catalog_does_not_render():
    # No Attribute node carries top_values on the 1.2 graph, so the renderer prints none: the prompt must say the
    # spellings are unknown rather than tell the agent to read them off a list.
    for name in GRAPH_FILES:
        assert "unknown, not absent" in shipped(name)
    assert "listed values show the spellings in use" not in shipped("graph_schema_structure.txt")


def test_the_prompt_ends_open_so_a_variant_can_append_a_section():
    agent = shipped("graph_agent.txt").rstrip()
    assert "END OF INSTRUCTIONS" not in agent.upper()
    assert "unless a section below adds another" in agent


# --- the API agent can use graph_search, and the tool permits it -----------------------------------------------------


def test_graph_search_is_advertised_and_the_read_only_tool_permits_it():
    endpoints = {e["path"]: e for e in json.loads(shipped("min_api_endpoints_enriched.json"))}
    entry = endpoints[GRAPH_SEARCH]
    assert entry["method"] == "POST"
    assert set(entry["request_body"]) >= {"filter_searchText", "extensions"}
    assert _is_read_only_request(GRAPH_SEARCH, "POST")


def test_the_api_prompt_states_graph_search_s_operators_and_hop_limit_as_the_model_does():
    models = read(REPO_ROOT / "nextseek_api" / "models.py")
    ops = re.search(r'op: Literal\[([^\]]*)\]', models).group(1)
    ops = [o.strip().strip('"') for o in ops.split(",")]
    api = shipped("api_agent.txt")
    stated = api[api.index('"op" is one of'):]
    stated = stated[:stated.index("\n")]
    for op in ops:
        assert op in stated, op
    bound = re.search(r"max_hops: int = Field\(\s*default=\d+, ge=1, le=(\d+)", models)
    assert bound, "the lineage model states its hop bound"
    le = bound.group(1)
    assert f'"max_hops": 1-{le}' in api and f"At most {le} hops" in api
    directions = re.search(r'direction: Literal\[([^\]]*)\]', models).group(1)
    lineage = api[api.index('"lineage"'):]
    lineage = lineage[:lineage.index("\n")]
    for direction in (d.strip().strip('"') for d in directions.split(",")):
        assert f'"{direction}"' in lineage, direction


def test_the_api_prompt_discloses_that_total_and_rows_can_disagree():
    api = shipped("api_agent.txt")
    assert "total is the graph's count of every match" in api
    assert "rows are one page read from the database" in api
