"""The v3 prompt variant (prompts/variants/v3/): v2 plus the fixes from the review of the 30 unforced Pilot A v2
turns (2026-09-18).

v3 inherits v2 and overrides four files. The graph agent prompt and its schema header carry the query-side fixes; the
parser keeps v2's routing (30 of 30 routed correctly) and changes only examples that quoted evaluation questions, plus
the wrapper's output schema. Every file is read by path from this checkout.

The worked examples were each run read-only against the local 1.2 graph when the prompt was written. One of them is
here because running it caught a trap: a path and an assay test on the same edge in one MATCH pattern. Cypher never
matches one relationship twice in a pattern, so "mice with a descendant that underwent Tissue Collection" returned 45
mice instead of 5,699. The assay test now sits in its own nested EXISTS, and a test below keeps it there.
"""

import json
import re
from pathlib import Path
from types import MappingProxyType

import pytest

from chat_nextseek import cypher_text
from chat_nextseek import graph_catalog as gcat
from chat_nextseek import prompt_variants as pv
from chat_nextseek.agents.graph import catalog_unknown_properties, whole_node_returns

NESSIE = Path(__file__).resolve().parents[2]
PACKAGE = NESSIE / "chat_nextseek" / "src" / "chat_nextseek"
PROMPTS = PACKAGE / "prompts"
V2 = PROMPTS / "variants" / "v2"
V3 = PROMPTS / "variants" / "v3"
CORPUS = NESSIE / "tests" / "nessie_tests" / "corpus.json"
GRAPH_FILES = ("graph_agent.txt", "graph_schema_structure.txt")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def v3(name: str) -> str:
    return read(V3 / name)


def _fenced(text: str) -> list[str]:
    return [block.strip() for block in re.findall(r"```\n(.*?)```", text, re.S)]


# --------------------------------------------------------------------------- the variant and what it overrides


def test_variant_json_inherits_v2_and_keeps_the_projection():
    manifest = json.loads(v3("variant.json"))
    assert manifest["inherits"] == "v2"
    assert manifest["project_parser_plan"] is True
    assert set(manifest) <= {"description", "inherits", "project_parser_plan"}


def test_v3_overrides_exactly_the_four_files_and_takes_the_rest_from_v2():
    overridden = sorted(p.name for p in V3.iterdir() if p.name != "variant.json")
    assert overridden == ["graph_agent.txt", "graph_schema_structure.txt", "parser_agent.txt",
                          "parser_core_routing.txt"]
    variant = pv.load_variant("v3")
    for name in overridden:
        assert variant.resolve(name) == V3 / name
    for name in ("api_agent.txt", "min_graph_schema.json", "min_api_endpoints_enriched.json"):
        assert variant.resolve(name) == V2 / name


def test_the_committed_tree_still_validates():
    assert "v3" in pv.validate_tree()


# --------------------------------------------------------------------------- the worked examples

SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h", synced_at=None, has_usage=False,
    index=tuple(
        gcat.TypeIndexRow(title=t, label="T_" + re.sub(r"[^A-Za-z0-9_]", "_", t), name=None, clade=None,
                          sample_count=10, deprecated=False, attributes_with_values=1)
        for t in ("SLD", "MUS", "CHM", "D.IMG", "WTR", "AB", "TIS", "D.SEQ")
    ),
    guard=MappingProxyType({
        "T_SLD": frozenset({"Stain", "PercentNecrosis", "Scientist"}),
        "T_MUS": frozenset({"Treatment1", "Treatment2", "Treatment3", "Sex", "Scientist"}),
        "T_CHM": frozenset({"Vendor", "Concentration", "Scientist"}),
        "T_D_IMG": frozenset({"Scientist"}),
        "T_WTR": frozenset({"Dechlorinated", "CollectionDate", "Scientist"}),
        "T_AB": frozenset({"Analyte", "Scientist"}),
        "T_TIS": frozenset({"Organ", "Scientist"}),
        "T_D_SEQ": frozenset({"Scientist"}),
    }),
)
EXAMPLES = [c for name in GRAPH_FILES for c in _fenced(v3(name))]


def test_the_graph_prompt_has_the_mixed_recipe_and_its_examples():
    agent = v3("graph_agent.txt")
    assert "### Filters and relationships together" in agent
    assert len(_fenced(agent)) >= 6


@pytest.mark.parametrize("cypher", EXAMPLES)
def test_every_worked_example_passes_the_write_check_and_both_guards(cypher):
    assert cypher_text.write_clause(cypher) is None
    assert whole_node_returns(cypher) == []
    assert catalog_unknown_properties(cypher, SNAPSHOT) == []


@pytest.mark.parametrize("name", GRAPH_FILES)
def test_every_variable_length_path_is_bounded_and_none_starts_at_zero(name):
    hops = re.findall(r"DERIVED_FROM\s*\*[^\]\s]*", v3(name))
    assert hops
    for hop in hops:
        assert re.fullmatch(r"DERIVED_FROM\s*\*[1-9]\d*\.\.\d+", hop), hop


@pytest.mark.parametrize("cypher", EXAMPLES)
def test_no_worked_example_has_an_unlabelled_node(cypher):
    # An unlabelled end also matches retired OrphanSample nodes (the multi-parent count was 11 too high).
    assert "()" not in cypher


@pytest.mark.parametrize("cypher", EXAMPLES)
def test_no_worked_example_tests_an_assay_on_an_edge_its_own_path_may_use(cypher):
    for line in cypher.splitlines():
        assert not (re.search(r"DERIVED_FROM\*", line) and re.search(r"\[r:DERIVED_FROM\]", line)), line


def test_the_lineage_direction_is_written_with_both_ends_labelled():
    assert "`(child:Sample)-[:DERIVED_FROM]->(parent:Sample)`" in v3("graph_agent.txt")
    assert "(child:Sample)-[:DERIVED_FROM]->(parent:Sample), both ends labelled" in v3("graph_schema_structure.txt")
    assert "so no sample query sees it" not in v3("graph_schema_structure.txt")


# --------------------------------------------------------------------------- the operator's rulings on the 30


def test_associated_with_is_a_text_search_and_a_guessed_type_is_not_a_scope():
    agent = v3("graph_agent.txt")
    assert "not the antibody type the entity step inferred" in agent
    assert "Follow DERIVED_FROM only when the question states the relationship" in agent
    assert '"associated with" alone is a text search' in agent


def test_the_zero_ladder_keeps_a_named_technique_s_zero_and_reads_the_uid_check():
    agent = v3("graph_agent.txt")
    ladder = agent[agent.index("## When a query matched nothing"):agent.index("## Rules for every query")]
    assert "Never replace it with a related technique" in ladder
    assert "UID CHECK" in ladder


def test_the_tool_total_is_named_as_a_row_count():
    assert "The tool's total counts rows, not samples." in v3("graph_agent.txt")


def test_the_prompt_ends_open_so_a_variant_can_append_a_section():
    assert "unless a section below adds another" in v3("graph_agent.txt")


# --------------------------------------------------------------------------- the parser: routing unchanged


def _corpus_queries() -> list[str]:
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("queries", "query", "question") and isinstance(value, (str, list)):
                    out.extend([value] if isinstance(value, str) else [v for v in value if isinstance(v, str)])
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(read(CORPUS)))
    return out


def _grams(text: str, n: int = 5) -> set[str]:
    words = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _graph_path(text: str) -> str:
    return text[text.index("PATH: graph_query"):text.index("PATH: new_search")]


def test_the_routing_core_differs_from_v2_only_in_ten_example_lines():
    old, new = v3("parser_core_routing.txt").splitlines(), read(V2 / "parser_core_routing.txt").splitlines()
    assert len(old) == len(new)
    changed = [i for i, (a, b) in enumerate(zip(old, new), 1) if a != b]
    assert len(changed) == 10, changed


def test_no_graph_routing_example_quotes_a_corpus_question():
    corpus = set().union(*(_grams(q) for q in _corpus_queries()))
    assert corpus, "the corpus has questions"
    examples = re.findall(r'"([^"]{12,})"', _graph_path(v3("parser_core_routing.txt")))
    assert examples
    for example in examples:
        assert not (_grams(example) & corpus), example


def test_the_wrapper_differs_from_the_default_only_by_the_lab_fields():
    default = read(PROMPTS / "parser_agent.txt")
    wrapper = v3("parser_agent.txt")
    added = [line.strip() for line in wrapper.splitlines() if line not in default.splitlines()]
    assert sorted(added) == sorted(['"projects": ["string"],', '"labs": ["string"],', '"lab_codes": ["string"]',
                                    '"uids": [string],', '"lab_codes": [string]'])
    assert pv.PARSER_CORE_PLACEHOLDER in wrapper
