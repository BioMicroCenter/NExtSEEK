"""The v2 prompt variant (prompts/variants/v2/): it loads, it keeps every placeholder the code fills, and it agrees with
the code that runs its output.

Every file is read by path from this checkout, never through ``chat_nextseek.__file__``, so the test checks the tree
it sits in even when an installed copy of the package is first on the path. The guard imports only supply the
property sets and the pure guard functions the prompts must satisfy.

What v2 changes, and why the parser part is surgical: every sample-metadata question routes to graph_query, and the
parser's other modes (system_question, reporter, follow-ups, pipeline, unsupported) keep their default text byte for
byte. The tests below pin both halves.
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
V2 = PROMPTS / "variants" / "v2"

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


def v2(name: str) -> str:
    return read(V2 / name)


def overrides() -> list[str]:
    return sorted(p.name for p in V2.iterdir() if p.name != "variant.json")


# --- the variant loads -----------------------------------------------------------------------------------------------


def test_variant_json_is_the_loader_contract():
    meta = json.loads(v2("variant.json"))
    assert set(meta) == {"description", "inherits", "project_parser_plan", "graph_agent_addendum",
                         "allowed_procedures"}
    assert meta["inherits"] is None
    assert meta["project_parser_plan"] is True
    assert meta["graph_agent_addendum"] is None
    assert meta["allowed_procedures"] == []
    assert isinstance(meta["description"], str) and meta["description"].strip()


def test_the_variant_carries_only_files_the_loader_knows_and_each_has_a_default():
    names = overrides()
    assert names, "v2 overrides nothing"
    assert set(names) <= set(DEFAULTS), f"unknown files: {set(names) - set(DEFAULTS)}"
    for name in names:
        assert DEFAULTS[name].is_file(), f"{name} has no default at {DEFAULTS[name]}"
        assert v2(name).strip(), f"{name} is empty"


def test_the_parser_wrappers_are_not_overridden():
    # The operator asked for a surgical parser change: the routing core and the parser's graph view only.
    assert "parser_agent.txt" not in overrides()
    assert "multi_parser_agent.txt" not in overrides()


@pytest.mark.parametrize("wrapper", ["parser_agent.txt", "multi_parser_agent.txt"])
def test_the_default_wrappers_compose_with_the_v2_routing_core(wrapper):
    default = read(DEFAULTS[wrapper])
    assert default.count(PLACEHOLDER) == 1
    core = v2("parser_core_routing.txt")
    composed = default.replace(PLACEHOLDER, core)
    assert PLACEHOLDER not in composed
    assert "{{" not in composed and "}}" not in composed
    assert core in composed


@pytest.mark.parametrize("name", ["graph_agent.txt", "graph_schema_structure.txt", "parser_core_routing.txt",
                                  "api_agent.txt"])
def test_no_text_override_carries_a_template_marker(name):
    # Only the two parser wrappers are templated ({{NAME}} markers), and v2 overrides neither. JSON braces are fine.
    assert not re.search(r"\{\{[A-Z_]+\}\}", v2(name))


def test_the_json_overrides_parse_and_keep_the_default_shape():
    schema, default_schema = json.loads(v2("min_graph_schema.json")), json.loads(read(DEFAULTS["min_graph_schema.json"]))
    assert set(schema) == set(default_schema)
    for key, value in default_schema.items():
        assert type(schema[key]) is type(value), key

    endpoints = json.loads(v2("min_api_endpoints_enriched.json"))
    default_endpoints = json.loads(read(DEFAULTS["min_api_endpoints_enriched.json"]))
    assert all(isinstance(e, dict) and e.get("path") and e.get("method") for e in endpoints)
    assert [e["path"] for e in default_endpoints] == [e["path"] for e in endpoints if e["path"] != GRAPH_SEARCH]
    assert {e["path"]: e["method"] for e in default_endpoints}.items() <= \
        {e["path"]: e["method"] for e in endpoints}.items()


# --- the parser change is surgical -----------------------------------------------------------------------------------


def _section(text: str, header: str) -> str:
    """One PATH section of the routing core, up to the next PATH or the HOW TO CHOOSE heading."""
    start = text.index(f"PATH: {header}\n")
    ends = [i for i in (text.find("\nPATH: ", start + 1), text.find("\nHOW TO CHOOSE", start + 1)) if i != -1]
    return text[start:min(ends)]


@pytest.mark.parametrize("path", ["reporter", "refine_last_search", "ask_about_last_results", "unsupported"])
def test_the_other_parser_modes_keep_their_default_text(path):
    assert _section(v2("parser_core_routing.txt"), path) == _section(read(DEFAULTS["parser_core_routing.txt"]), path)


def test_the_entity_handling_and_the_write_and_export_steps_are_unchanged():
    core, default = v2("parser_core_routing.txt"), read(DEFAULTS["parser_core_routing.txt"])
    for anchor in ("ENTITY HANDLING (HARD RULE)", "1. Is the user asking to create, register, add",
                   "2. Is the user asking for an unscoped bulk export", "4. Is the user asking for aggregate statistics",
                   "5. Is the user referring back to results", "Repository-style deliverables belong under reporter"):
        para = lambda t: t[t.index(anchor):t.index("\n\n", t.index(anchor))]  # noqa: E731
        assert para(core) == para(default), anchor


def test_the_routing_core_sends_sample_metadata_to_the_graph():
    core = v2("parser_core_routing.txt")
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
    schema = json.loads(v2("min_graph_schema.json"))
    default = json.loads(read(DEFAULTS["min_graph_schema.json"]))
    sample = next(n for n in schema["node_types"] if n["label"] == "Sample")["description"]
    for claim in ("1.2", "search_text", "typed", "lab"):
        assert claim in sample, claim
    assert {"IN_PROJECT"} <= {r["type"] for r in schema["relationships"]}
    for rule in schema["disambiguation_rules"] + schema["api_preferred_triggers"]:
        assert "advanced_search endpoint" not in rule and "prefer API" not in rule, rule
        assert not re.search(r"→ API\b", rule), rule
    # Surgical: the entries v2 did not need to change are the default's, byte for byte.
    assert [n for n in schema["node_types"] if n["label"] in ("Study", "Investigation")] == default["node_types"][1:]
    assert schema["relationships"][:3] == default["relationships"]
    assert schema["graph_query_triggers"][:len(default["graph_query_triggers"])] == default["graph_query_triggers"]
    kept = [0, 1, 4, 6, 8, 9, 10]
    assert [schema["disambiguation_rules"][i] for i in kept] == [default["disambiguation_rules"][i] for i in kept]


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


def test_the_graph_prompt_has_worked_examples():
    assert len(_fenced(v2("graph_agent.txt"))) >= 4


@pytest.mark.parametrize("cypher", [c for name in GRAPH_FILES for c in _fenced(v2(name))])
def test_every_worked_example_passes_the_write_check_and_both_guards(cypher):
    assert cypher_text.write_clause(cypher) is None
    assert whole_node_returns(cypher) == []
    assert catalog_unknown_properties(cypher, SNAPSHOT) == []


@pytest.mark.parametrize("name", GRAPH_FILES)
def test_every_variable_length_path_is_bounded(name):
    text = v2(name)
    hops = re.findall(r"DERIVED_FROM\s*\*[^\]\s]*", text)
    assert hops, "the lineage rules name a path"
    for hop in hops:
        assert re.fullmatch(r"DERIVED_FROM\s*\*\d+\.\.\d+", hop), hop


def test_the_bound_is_the_measured_longest_chain_plus_one():
    # Measured on the 1.2 graph: chains of 11 hops exist, none of 12, so *1..12 reaches every ancestor.
    agent = v2("graph_agent.txt")
    assert "11 hops" in agent and "[:DERIVED_FROM*1..12]" in agent
    assert "11 hops" in v2("graph_schema_structure.txt")


def test_the_system_properties_the_prompt_names_are_the_guard_s():
    agent = v2("graph_agent.txt")
    line = next(l for l in agent.splitlines() if l.startswith("- **System properties**"))
    named = set(re.findall(r"`([a-z_]+)`", line.split(".", 1)[0] + line))
    assert V12_SYSTEM_PROPERTIES <= named
    structure = v2("graph_schema_structure.txt")
    sample = structure[structure.index("(:Sample:T_<code>)"):structure.index("(:SampleType")]
    for prop in V12_SYSTEM_PROPERTIES:
        assert re.search(rf"\b{prop}\b", sample), prop


def test_every_property_the_structure_lists_on_another_label_is_allowed_by_the_guard():
    structure = v2("graph_schema_structure.txt")
    for label, props in re.findall(r"\(:([A-Za-z]+) \{([^}]*)\}", structure):
        names = {p.strip() for p in props.replace("\n", " ").split(",") if p.strip()}
        allowed = V11_NODE_PROPERTIES.get(label) or V11_RELATIONSHIP_PROPERTIES.get(label)
        assert allowed is not None, label
        assert names <= allowed, (label, names - allowed)
    for rel, props in re.findall(r"\[:([A-Z_]+) \{([^}]*)\}\]", structure):
        names = {p.strip() for p in props.split(",")}
        assert names <= V11_RELATIONSHIP_PROPERTIES[rel], (rel, names - V11_RELATIONSHIP_PROPERTIES[rel])


def test_list_properties_are_carved_out_of_the_to_string_rule():
    agent = v2("graph_agent.txt")
    rule = agent[agent.index("**Never compare free text with a bare `=`.**"):]
    rule = rule[:rule.index("\n")]
    for prop in ("project_ids", "parent_titles", "parent_title_hashes"):
        assert prop in rule


def test_the_lab_code_is_matched_after_the_date_not_at_the_start():
    agent = v2("graph_agent.txt")
    assert "<TYPE>-<YYMMDD><LAB>-<n>" in agent
    assert "s.uuid =~ ('(?i)^[^-]+-[0-9]{6}' + $lab + '-.*')" in agent
    assert "never at the start" in agent


def test_person_names_never_go_to_person_nodes_or_the_people_endpoint():
    agent = v2("graph_agent.txt")
    assert "`Scientist` attribute" in agent and "Never use `Person` nodes for a name" in agent
    api = v2("api_agent.txt")
    assert "NEVER call /nextseek_api/people/ to find samples by a person" in api


def test_the_prompt_never_promises_a_values_list_the_catalog_does_not_render():
    # No Attribute node carries top_values on the 1.2 graph, so the renderer prints none: the prompt must say the
    # spellings are unknown rather than tell the agent to read them off a list.
    for name in GRAPH_FILES:
        assert "unknown, not absent" in v2(name)
    assert "listed values show the spellings in use" not in v2("graph_schema_structure.txt")


def test_the_prompt_ends_open_so_a_variant_can_append_a_section():
    agent = v2("graph_agent.txt").rstrip()
    assert "END OF INSTRUCTIONS" not in agent.upper()
    assert "unless a section below adds another" in agent


# --- the API agent can use graph_search, and the tool permits it -----------------------------------------------------


def test_graph_search_is_advertised_and_the_read_only_tool_permits_it():
    endpoints = {e["path"]: e for e in json.loads(v2("min_api_endpoints_enriched.json"))}
    entry = endpoints[GRAPH_SEARCH]
    assert entry["method"] == "POST"
    assert set(entry["request_body"]) >= {"filter_searchText", "extensions"}
    assert _is_read_only_request(GRAPH_SEARCH, "POST")


def test_the_api_prompt_states_graph_search_s_operators_and_hop_limit_as_the_model_does():
    models = read(REPO_ROOT / "nextseek_api" / "models.py")
    ops = re.search(r'op: Literal\[([^\]]*)\]', models).group(1)
    ops = [o.strip().strip('"') for o in ops.split(",")]
    api = v2("api_agent.txt")
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
    api = v2("api_agent.txt")
    assert "total is the graph's count of every match" in api
    assert "rows are one page read from the database" in api
