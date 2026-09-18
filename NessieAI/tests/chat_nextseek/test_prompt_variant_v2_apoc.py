"""The v2_apoc prompt variant (prompts/variants/v2_apoc/): v2 plus an APOC section, and nothing else.

The operator runs v2 and v2_apoc on the same questions to see whether APOC helps, so the only difference between
the two arms must be APOC: v2_apoc inherits v2, adds one graph agent section and three allowed procedures, and
overrides no file. v2 is written on its own branch; these tests load v2_apoc through the real loader over a
stand-in v2 in a temporary tree, and the tests that need the real v2 skip until it is in this checkout.

Every file is read by path from this checkout. Nothing here reaches Neo4j or a model.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from chat_nextseek import cypher_text
from chat_nextseek import graph_catalog as gcat
from chat_nextseek import prompt_variants as pv
from chat_nextseek.agents.graph import catalog_unknown_properties, whole_node_returns
from chat_nextseek.helpers.tools.neo4j import tool_neo4j_query

NESSIE = Path(__file__).resolve().parents[2]
PROMPTS = NESSIE / "chat_nextseek" / "src" / "chat_nextseek" / "prompts"
VARIANTS = PROMPTS / "variants"
V2_APOC = VARIANTS / "v2_apoc"
ADDENDUM_NAME = "graph_agent_apoc.txt"
ALLOWED = ("apoc.path.subgraphNodes", "apoc.path.spanningTree", "apoc.path.expandConfig")
NO_PASSWORD = "NEO4J_PASSWORD not configured"
STAND_IN_V2 = ("STAND-IN V2 GRAPH PROMPT\nThe only procedure you may CALL is `db.index.fulltext.queryNodes`, unless a "
               "section below adds another.\n")


def manifest() -> dict:
    return json.loads((V2_APOC / "variant.json").read_text(encoding="utf-8"))


def addendum() -> str:
    return (V2_APOC / ADDENDUM_NAME).read_text(encoding="utf-8")


def fenced(text: str) -> list[str]:
    return [block.strip() for block in re.findall(r"```\n(.*?)```", text, re.S)]


@pytest.fixture
def tree(tmp_path) -> Path:
    """A variants tree holding the committed v2_apoc and a stand-in v2 (never committed)."""
    root = tmp_path / "variants"
    shutil.copytree(V2_APOC, root / "v2_apoc")
    (root / "v2").mkdir()
    (root / "v2" / "graph_agent.txt").write_text(STAND_IN_V2, encoding="utf-8")
    (root / "v2" / "variant.json").write_text(json.dumps({"inherits": None, "project_parser_plan": True}),
                                              encoding="utf-8")
    return root


def _config(**attrs):
    base = dict(GRAPH_AGENT_SYSTEM_PROMPT="DEFAULT GRAPH PROMPT\n", API_AGENT_SYSTEM_PROMPT="DEFAULT API\n",
                PARSER_CORE_ROUTING_PROMPT="core", PARSER_SYSTEM_PROMPT="parser", MULTI_PARSER_SYSTEM_PROMPT="multi",
                MIN_GRAPH_SCHEMA={}, MIN_API_ENDPOINTS=[], ENDPOINT_INDEX=None, PROMPTS_DIR=str(PROMPTS),
                NEO4J_PASSWORD=None, NEO4J_URI="bolt://nowhere:7687", NEO4J_USER="neo4j")
    base.update(attrs)
    return SimpleNamespace(**base)


def well_formed(proc: str) -> str:
    unique = ", uniqueness: 'NODE_GLOBAL'" if proc == "apoc.path.expandConfig" else ""
    field = "node" if proc == "apoc.path.subgraphNodes" else "path"
    return (f"MATCH (x:Sample {{uuid: $uid}}) CALL {proc}(x, {{relationshipFilter: 'DERIVED_FROM>', "
            f"labelFilter: '+Sample', maxLevel: 12{unique}}}) YIELD {field} RETURN count(*) AS n")


# --- the manifest and the directory ---------------------------------------------------------------------------------


def test_the_manifest_inherits_v2_and_adds_only_apoc():
    meta = manifest()
    assert set(meta) == {"description", "inherits", "graph_agent_addendum", "allowed_procedures"}
    assert meta["inherits"] == "v2"
    assert meta["graph_agent_addendum"] == ADDENDUM_NAME
    assert tuple(meta["allowed_procedures"]) == ALLOWED
    assert "project_parser_plan" not in meta, "stated here it could differ from v2; left out, it is inherited"
    assert meta["description"].strip()


def test_the_directory_overrides_no_v2_file():
    assert sorted(p.name for p in V2_APOC.iterdir()) == sorted([ADDENDUM_NAME, "variant.json"])


# --- the real loader, over a stand-in v2 ----------------------------------------------------------------------------


def test_the_variant_loads_through_the_real_loader(tree):
    variant = pv.load_variant("v2_apoc", variants_dir=tree)
    assert variant.inherits == "v2"
    assert variant.allowed_procedures == ALLOWED
    assert variant.addendum == tree / "v2_apoc" / ADDENDUM_NAME
    assert variant.chain == (tree / "v2_apoc", tree / "v2")
    assert variant.project_parser_plan is True  # inherited from v2
    assert set(pv.validate_tree(variants_dir=tree)) == {"v2", "v2_apoc"}


def test_the_copy_is_v2_plus_the_section_and_the_procedures(tree):
    singleton = _config()
    v2 = pv.apply_variant(singleton, "v2", variants_dir=tree)
    apoc = pv.apply_variant(singleton, "v2_apoc", variants_dir=tree)

    assert apoc.GRAPH_AGENT_SYSTEM_PROMPT == STAND_IN_V2.rstrip("\n") + "\n\n" + addendum()
    assert apoc.EXTRA_ALLOWED_PROCEDURES == frozenset(ALLOWED) and v2.EXTRA_ALLOWED_PROCEDURES == frozenset()
    assert apoc.PROMPT_VARIANT_FILES == {**v2.PROMPT_VARIANT_FILES,
                                         "graph_agent_addendum": f"v2_apoc/{ADDENDUM_NAME}"}
    differing = {k for k in vars(apoc) if getattr(apoc, k) != getattr(v2, k)}
    assert differing == {"GRAPH_AGENT_SYSTEM_PROMPT", "EXTRA_ALLOWED_PROCEDURES", "PROMPT_VARIANT",
                         "PROMPT_VARIANT_FILES"}
    assert singleton.GRAPH_AGENT_SYSTEM_PROMPT == "DEFAULT GRAPH PROMPT\n"


def test_the_real_v2_when_this_checkout_has_it():
    """Live once v2 (feat/nessie-v2-prompts) is merged beside v2_apoc: the committed tree then loads whole."""
    if not (VARIANTS / "v2").is_dir():
        pytest.skip("prompts/variants/v2 is not in this checkout yet")
    loaded = pv.validate_tree()
    assert {"v2", "v2_apoc"} <= set(loaded)
    v2_prompt = (VARIANTS / "v2" / "graph_agent.txt").read_text(encoding="utf-8")
    assert "unless a section below adds another" in v2_prompt
    assert "11 hops" in v2_prompt and "[:DERIVED_FROM*1..12]" in v2_prompt


# --- the procedures: allowed only under the variant, and only these -------------------------------------------------


@pytest.mark.parametrize("proc", ALLOWED)
def test_each_allowed_procedure_passes_only_under_the_variant(tree, proc):
    q = well_formed(proc)
    assert cypher_text.write_clause(q) == f"CALL {proc}"
    assert cypher_text.write_clause(q, extra_procedures=frozenset(ALLOWED)) is None

    singleton = _config()
    variant = pv.apply_variant(singleton, "v2_apoc", variants_dir=tree)
    assert tool_neo4j_query(variant, q, {"uid": "X"})["error"] == NO_PASSWORD  # past the text check
    assert f"Refused: CALL {proc}" in tool_neo4j_query(singleton, q, {"uid": "X"})["error"]
    assert f"Refused: CALL {proc}" in tool_neo4j_query(pv.apply_variant(singleton, "v2", variants_dir=tree),
                                                       q, {"uid": "X"})["error"]


@pytest.mark.parametrize("q, refused", [
    ("MATCH (x:Sample) CALL apoc.path.expand(x, 'DERIVED_FROM>', '+Sample', 1, 4) YIELD path RETURN count(*)",
     "CALL apoc.path.expand"),
    ("MATCH (x:Sample) CALL apoc.path.subgraphAll(x, {relationshipFilter: 'DERIVED_FROM>', maxLevel: 2}) "
     "YIELD nodes RETURN size(nodes)", "CALL apoc.path.subgraphAll"),
    ("CALL apoc.meta.schema() YIELD value RETURN value", "CALL apoc.meta.schema"),
    ("CALL apoc.meta.stats() YIELD labels RETURN labels", "CALL apoc.meta.stats"),
    ("CALL apoc.meta.data() YIELD label RETURN label", "CALL apoc.meta.data"),
    ("CALL apoc.stats.degrees('DERIVED_FROM') YIELD max RETURN max", "CALL apoc.stats.degrees"),
    ("MATCH (s:Sample) CALL apoc.convert.setJsonProperty(s, 'x', 1) RETURN 1", "CALL apoc.convert.setJsonProperty"),
    ("CALL apoc.coll.zipToRows([1], [2]) YIELD value RETURN value", "CALL apoc.coll.zipToRows"),
    ("CALL apoc.cypher.run('MATCH (n) RETURN n', {}) YIELD value RETURN value", "CALL apoc.cypher.run"),
    ("CALL db.labels() YIELD label RETURN label", "CALL db.labels"),
])
def test_every_other_procedure_stays_refused_under_the_variant(q, refused):
    assert cypher_text.write_clause(q, extra_procedures=frozenset(ALLOWED)) == refused
    assert cypher_text.write_clause(q) == refused


@pytest.mark.parametrize("name", ["apoc.convert.setJsonProperty", "apoc.nodes.link"])
def test_the_loader_refuses_the_writing_procedures_this_server_loads(tmp_path, name):
    (tmp_path / "v2_apoc").mkdir()
    (tmp_path / "v2_apoc" / "variant.json").write_text(json.dumps({"allowed_procedures": [name]}), encoding="utf-8")
    with pytest.raises(pv.VariantError, match="allowed_procedures"):
        pv.load_variant("v2_apoc", variants_dir=tmp_path)


# --- the section itself ---------------------------------------------------------------------------------------------


def _row(title):
    label = "T_" + re.sub(r"[^A-Za-z0-9_]", "_", title)
    return gcat.TypeIndexRow(title=title, label=label, name=None, clade=None, sample_count=10, deprecated=False,
                             attributes_with_values=1)


# The types the worked shapes name; they read only system properties.
SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h", synced_at=None, has_usage=False,
    index=tuple(_row(t) for t in ("D.SPC", "WTR", "TIS", "D.SEQ")),
    guard=MappingProxyType({"T_D_SPC": frozenset(), "T_WTR": frozenset(), "T_TIS": frozenset(),
                            "T_D_SEQ": frozenset({"Sequencer"})}),
)


def test_the_section_has_a_worked_shape_for_each_procedure():
    blocks = fenced(addendum())
    assert len(blocks) >= 4
    for proc in ALLOWED:
        assert any(f"CALL {proc}(" in b for b in blocks), proc


@pytest.mark.parametrize("cypher", fenced(addendum()))
def test_every_worked_shape_passes_every_guard_under_the_variant(cypher):
    extra = frozenset(ALLOWED)
    assert cypher_text.write_clause(cypher, extra_procedures=extra) is None
    assert cypher_text.procedure_call_problems(cypher, extra) == []
    assert whole_node_returns(cypher, extra) == []
    assert catalog_unknown_properties(cypher, SNAPSHOT) == []


@pytest.mark.parametrize("cypher", fenced(addendum()))
def test_every_worked_shape_is_bounded_filtered_and_skips_orphans(cypher):
    for call in re.findall(r"CALL (apoc\.path\.\w+)\((.*?)\}\)", cypher, re.S):
        proc, config = call
        level = re.search(r"maxLevel: (\d+)", config)
        assert level and 1 <= int(level.group(1)) <= cypher_text.APOC_PATH_MAX_LEVEL, proc
        assert re.search(r"relationshipFilter: '<?DERIVED_FROM>?'", config), proc
        assert "labelFilter: '+Sample" in config, proc


def test_the_section_names_only_the_allowed_procedures_and_no_meta():
    text = addendum()
    assert set(re.findall(r"apoc\.path\.\w+", text)) == set(ALLOWED)
    assert "apoc.meta" not in text
    for proc in ALLOWED:
        assert proc in text


def test_the_section_states_the_guard_s_bound_and_the_measured_depth():
    text = addendum()
    assert cypher_text.APOC_PATH_MAX_LEVEL == 12
    assert "from 1 to 12" in text and "11 hops" in text
    assert "'NODE_GLOBAL'" in text


def test_the_section_follows_the_v2_procedure_rule():
    text = addendum()
    assert text.startswith("---\n\n## APOC path procedures (this section adds three procedures)")
    assert "Besides `db.index.fulltext.queryNodes`" in text


def test_the_section_carries_no_sample_uid():
    """No evaluation content: the worked shapes are parameterised, never a real sample."""
    assert not re.search(r"\b[A-Z][A-Z.]*-\d{6}[A-Z]{3}-\d+", addendum())


def test_the_section_teaches_clean_with_its_measured_limits():
    text = addendum()
    assert "apoc.text.clean" in text
    assert "`-28` into `28`" in text and "`CD4+` equal `CD4-`" in text
    assert "levenshteinSimilarity" in text and "0.17" in text
