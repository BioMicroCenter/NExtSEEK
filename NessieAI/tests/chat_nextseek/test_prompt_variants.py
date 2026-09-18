"""Prompt variants: an evaluation-only alternative prompt set for one turn (``chat_nextseek.prompt_variants``).

A variant is a directory under ``prompts/variants/<name>/``. ``apply_variant`` returns a shallow copy of the
ChatConfig whose prompts and parser/API context files come from that directory, then from the directory it
``inherits``, then from the defaults. The shared singleton is never touched. Every test builds its variant tree
under ``tmp_path``; nothing here writes into the package's own ``prompts/variants/``, which the prompt writers own.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import chat_nextseek
from chat_nextseek import prompt_variants as pv
from chat_nextseek.config import ChatConfig, compose_parser_prompt

PACKAGE = Path(chat_nextseek.__file__).resolve().parent

DEFAULT_FILES = {
    "graph_agent.txt": "DEFAULT GRAPH PROMPT\n",
    "api_agent.txt": "DEFAULT API PROMPT\n",
    "parser_core_routing.txt": "DEFAULT CORE ROUTING",
    "parser_agent.txt": "PARSER WRAPPER START\n{{PARSER_CORE_ROUTING}}\nPARSER WRAPPER END\n",
    "multi_parser_agent.txt": "MULTI WRAPPER START\n{{PARSER_CORE_ROUTING}}\nMULTI WRAPPER END\n",
}


class FakeConfig:
    """The attributes a variant replaces, composed the way ChatConfig composes them."""

    def __init__(self, prompts_dir: Path):
        self.PROMPTS_DIR = str(prompts_dir)
        read = lambda n: (prompts_dir / n).read_text(encoding="utf-8")  # noqa: E731
        self.PARSER_CORE_ROUTING_PROMPT = read("parser_core_routing.txt")
        self.PARSER_SYSTEM_PROMPT = compose_parser_prompt(read("parser_agent.txt"), self.PARSER_CORE_ROUTING_PROMPT)
        self.MULTI_PARSER_SYSTEM_PROMPT = compose_parser_prompt(read("multi_parser_agent.txt"),
                                                                self.PARSER_CORE_ROUTING_PROMPT)
        self.GRAPH_AGENT_SYSTEM_PROMPT = read("graph_agent.txt")
        self.API_AGENT_SYSTEM_PROMPT = read("api_agent.txt")
        self.MIN_GRAPH_SCHEMA = {"schema": "default"}
        self.MIN_API_ENDPOINTS = [{"path": "/nextseek_api/default/", "method": "GET"}]
        self.ENDPOINT_INDEX = None
        self.shared = {"catalog": [1, 2, 3]}


@pytest.fixture
def defaults(tmp_path) -> Path:
    d = tmp_path / "prompts"
    d.mkdir()
    for name, text in DEFAULT_FILES.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


@pytest.fixture
def config(defaults) -> FakeConfig:
    return FakeConfig(defaults)


@pytest.fixture
def root(tmp_path) -> Path:
    d = tmp_path / "variants"
    d.mkdir()
    return d


def _variant(root: Path, name: str, files: dict | None = None, manifest: dict | str | None = None) -> Path:
    d = root / name
    d.mkdir()
    for fname, content in (files or {}).items():
        text = json.dumps(content) if not isinstance(content, str) else content
        (d / fname).write_text(text, encoding="utf-8")
    if manifest is not None:
        text = manifest if isinstance(manifest, str) else json.dumps(manifest)
        (d / pv.MANIFEST).write_text(text, encoding="utf-8")
    return d


def _state(obj) -> dict:
    return {k: (id(v), copy.deepcopy(v)) for k, v in vars(obj).items()}


# --------------------------------------------------------------------------- the names and the layout


def test_the_variant_names_are_the_contract():
    assert pv.VARIANT_NAMES == ("v2", "v2_apoc")


def test_the_variants_live_under_the_package_prompts_directory():
    assert pv.VARIANTS_DIR == PACKAGE / "prompts" / "variants"


def test_the_overridable_files_are_the_contract():
    assert pv.PROMPT_FILES == {
        "graph_agent.txt", "graph_schema_structure.txt", "parser_core_routing.txt", "parser_agent.txt",
        "multi_parser_agent.txt", "api_agent.txt",
    }
    assert pv.JSON_FILES == {"min_graph_schema.json", "min_api_endpoints_enriched.json"}
    assert pv.MANIFEST == "variant.json"


def test_every_default_prompt_a_variant_can_replace_exists_in_the_package():
    for name in pv.PROMPT_FILES:
        assert (PACKAGE / "prompts" / name).is_file(), name
    for name in pv.JSON_FILES:
        assert (PACKAGE / "context" / name).is_file(), name


def test_the_repository_variant_tree_is_valid():
    """Every directory the prompt writers commit must be a known name and load cleanly.

    Vacuous until the first variant lands. An unknown directory, an unexpected file, a bad variant.json, a missing
    addendum or a broken inherits chain fails here, loudly, rather than being ignored at runtime.
    """
    loaded = pv.validate_tree()
    assert set(loaded) <= set(pv.VARIANT_NAMES)


# --------------------------------------------------------------------------- loud failures


def test_an_unknown_name_is_refused(root):
    _variant(root, "v3")
    with pytest.raises(pv.VariantError, match="unknown prompt variant 'v3'"):
        pv.load_variant("v3", variants_dir=root)


def test_a_missing_directory_is_refused(root):
    with pytest.raises(pv.VariantError, match="no directory"):
        pv.load_variant("v2", variants_dir=root)


def test_an_unexpected_file_is_refused(root):
    _variant(root, "v2", {"graph_agent.txt": "x", "notes.md": "scratch"})
    with pytest.raises(pv.VariantError, match="notes.md"):
        pv.load_variant("v2", variants_dir=root)


def test_a_subdirectory_is_refused(root):
    d = _variant(root, "v2")
    (d / "old").mkdir()
    with pytest.raises(pv.VariantError, match="old"):
        pv.load_variant("v2", variants_dir=root)


def test_an_unknown_directory_in_the_tree_is_refused(root):
    _variant(root, "v2")
    _variant(root, "v2-draft")
    with pytest.raises(pv.VariantError, match="v2-draft"):
        pv.validate_tree(variants_dir=root)


def test_a_stray_file_in_the_tree_is_refused(root):
    _variant(root, "v2")
    (root / "README.md").write_text("x", encoding="utf-8")
    with pytest.raises(pv.VariantError, match="README.md"):
        pv.validate_tree(variants_dir=root)


def test_an_absent_tree_validates_to_nothing(tmp_path):
    assert pv.validate_tree(variants_dir=tmp_path / "nowhere") == {}


@pytest.mark.parametrize("manifest, match", [
    ("{not json", "variant.json"),
    ([], "JSON object"),
    ({"descripton": "typo"}, "descripton"),
    ({"project_parser_plan": "true"}, "project_parser_plan"),
    ({"allowed_procedures": "apoc.path.expand"}, "allowed_procedures"),
    ({"inherits": 2}, "inherits"),
    ({"description": 7}, "description"),
])
def test_a_malformed_manifest_is_refused(root, manifest, match):
    _variant(root, "v2", manifest=manifest)
    with pytest.raises(pv.VariantError, match=match):
        pv.load_variant("v2", variants_dir=root)


@pytest.mark.parametrize("inherits, match", [
    ("v3", "unknown prompt variant 'v3'"),
    ("v2_apoc", "inherits itself"),
])
def test_a_bad_inherits_is_refused(root, inherits, match):
    _variant(root, "v2_apoc", manifest={"inherits": inherits})
    with pytest.raises(pv.VariantError, match=match):
        pv.load_variant("v2_apoc", variants_dir=root)


def test_an_inherits_cycle_is_refused(root):
    _variant(root, "v2", manifest={"inherits": "v2_apoc"})
    _variant(root, "v2_apoc", manifest={"inherits": "v2"})
    with pytest.raises(pv.VariantError, match="cycle"):
        pv.load_variant("v2_apoc", variants_dir=root)


def test_an_inherited_variant_must_exist(root):
    _variant(root, "v2_apoc", manifest={"inherits": "v2"})
    with pytest.raises(pv.VariantError, match="no directory"):
        pv.load_variant("v2_apoc", variants_dir=root)


def test_a_json_file_of_the_wrong_shape_is_refused(root, config):
    _variant(root, "v2", {"min_graph_schema.json": ["not", "a", "dict"]})
    with pytest.raises(pv.VariantError, match="min_graph_schema.json"):
        pv.apply_variant(config, "v2", variants_dir=root)
    _variant(root, "v2_apoc", {"min_api_endpoints_enriched.json": {"not": "a list"}})
    with pytest.raises(pv.VariantError, match="min_api_endpoints_enriched.json"):
        pv.apply_variant(config, "v2_apoc", variants_dir=root)


def test_unparseable_json_is_refused(root, config):
    _variant(root, "v2", {"min_graph_schema.json": "{oops"})
    with pytest.raises(pv.VariantError, match="min_graph_schema.json"):
        pv.apply_variant(config, "v2", variants_dir=root)


# --------------------------------------------------------------------------- file resolution


def test_a_variant_replaces_only_the_files_it_holds(root, config):
    _variant(root, "v2", {"graph_agent.txt": "V2 GRAPH", "api_agent.txt": "V2 API"})

    out = pv.apply_variant(config, "v2", variants_dir=root)

    assert out.GRAPH_AGENT_SYSTEM_PROMPT == "V2 GRAPH"
    assert out.API_AGENT_SYSTEM_PROMPT == "V2 API"
    assert out.PARSER_SYSTEM_PROMPT == config.PARSER_SYSTEM_PROMPT
    assert out.MULTI_PARSER_SYSTEM_PROMPT == config.MULTI_PARSER_SYSTEM_PROMPT
    assert out.MIN_GRAPH_SCHEMA is config.MIN_GRAPH_SCHEMA
    assert out.MIN_API_ENDPOINTS is config.MIN_API_ENDPOINTS


def test_lookup_is_the_variant_then_the_inherited_variant_then_the_default(root, config):
    _variant(root, "v2", {"graph_agent.txt": "V2 GRAPH", "api_agent.txt": "V2 API"})
    _variant(root, "v2_apoc", {"api_agent.txt": "APOC API"}, manifest={"inherits": "v2"})

    out = pv.apply_variant(config, "v2_apoc", variants_dir=root)

    assert out.API_AGENT_SYSTEM_PROMPT == "APOC API"            # its own
    assert out.GRAPH_AGENT_SYSTEM_PROMPT == "V2 GRAPH"          # inherited
    assert out.PARSER_SYSTEM_PROMPT == config.PARSER_SYSTEM_PROMPT  # the default
    assert out.PROMPT_VARIANT_FILES == {
        "api_agent.txt": "v2_apoc/api_agent.txt",
        "graph_agent.txt": "v2/graph_agent.txt",
    }


def test_the_json_files_replace_the_parser_and_api_context(root, config):
    schema = {"schema": "v2", "nodes": ["Sample"]}
    endpoints = [{"path": "/nextseek_api/samples/graph_search/", "method": "POST"}]
    _variant(root, "v2", {"min_graph_schema.json": schema, "min_api_endpoints_enriched.json": endpoints})

    out = pv.apply_variant(config, "v2", variants_dir=root)

    assert out.MIN_GRAPH_SCHEMA == schema
    assert out.MIN_API_ENDPOINTS == endpoints
    assert config.MIN_GRAPH_SCHEMA == {"schema": "default"}


def test_a_replaced_endpoint_catalog_drops_the_default_catalogs_semantic_index(root, config):
    """The index shortlists entries of the DEFAULT catalog; the variant's parser must see its own catalog."""
    config.ENDPOINT_INDEX = object()
    _variant(root, "v2", {"min_api_endpoints_enriched.json": [{"path": "/x/", "method": "GET"}]})
    _variant(root, "v2_apoc", {"graph_agent.txt": "G"})

    assert pv.apply_variant(config, "v2", variants_dir=root).ENDPOINT_INDEX is None
    assert pv.apply_variant(config, "v2_apoc", variants_dir=root).ENDPOINT_INDEX is config.ENDPOINT_INDEX


def test_the_graph_schema_structure_is_carried_on_the_copy(root, config):
    _variant(root, "v2", {"graph_schema_structure.txt": "V2 STRUCTURE\n\n"})

    out = pv.apply_variant(config, "v2", variants_dir=root)

    assert out.GRAPH_SCHEMA_STRUCTURE == "V2 STRUCTURE"
    assert not hasattr(config, "GRAPH_SCHEMA_STRUCTURE")


def test_a_variant_with_no_files_changes_no_prompt(root, config):
    _variant(root, "v2", manifest={"description": "empty"})

    out = pv.apply_variant(config, "v2", variants_dir=root)

    for attr in ("GRAPH_AGENT_SYSTEM_PROMPT", "API_AGENT_SYSTEM_PROMPT", "PARSER_SYSTEM_PROMPT",
                 "MULTI_PARSER_SYSTEM_PROMPT", "PARSER_CORE_ROUTING_PROMPT"):
        assert getattr(out, attr) == getattr(config, attr), attr
    assert out.PROMPT_VARIANT == "v2"
    assert out.PROMPT_VARIANT_FILES == {}


# --------------------------------------------------------------------------- re-composition of the parser prompts


def test_a_new_routing_core_recomposes_both_parser_prompts_from_the_default_wrappers(root, config):
    _variant(root, "v2", {"parser_core_routing.txt": "V2 CORE ROUTING"})

    out = pv.apply_variant(config, "v2", variants_dir=root)

    assert out.PARSER_CORE_ROUTING_PROMPT == "V2 CORE ROUTING"
    assert out.PARSER_SYSTEM_PROMPT == "PARSER WRAPPER START\nV2 CORE ROUTING\nPARSER WRAPPER END\n"
    assert out.MULTI_PARSER_SYSTEM_PROMPT == "MULTI WRAPPER START\nV2 CORE ROUTING\nMULTI WRAPPER END\n"
    assert "DEFAULT CORE ROUTING" not in out.PARSER_SYSTEM_PROMPT + out.MULTI_PARSER_SYSTEM_PROMPT


def test_a_new_wrapper_is_composed_with_the_default_core(root, config):
    _variant(root, "v2", {"parser_agent.txt": "V2 WRAPPER {{PARSER_CORE_ROUTING}} END"})

    out = pv.apply_variant(config, "v2", variants_dir=root)

    assert out.PARSER_SYSTEM_PROMPT == "V2 WRAPPER DEFAULT CORE ROUTING END"
    assert out.MULTI_PARSER_SYSTEM_PROMPT == config.MULTI_PARSER_SYSTEM_PROMPT


def test_an_inherited_core_is_composed_into_the_childs_wrapper(root, config):
    _variant(root, "v2", {"parser_core_routing.txt": "V2 CORE"})
    _variant(root, "v2_apoc", {"multi_parser_agent.txt": "APOC MULTI [{{PARSER_CORE_ROUTING}}]"},
             manifest={"inherits": "v2"})

    out = pv.apply_variant(config, "v2_apoc", variants_dir=root)

    assert out.MULTI_PARSER_SYSTEM_PROMPT == "APOC MULTI [V2 CORE]"
    assert out.PARSER_SYSTEM_PROMPT == "PARSER WRAPPER START\nV2 CORE\nPARSER WRAPPER END\n"


def test_a_core_that_no_wrapper_would_inject_is_refused(root, config):
    _variant(root, "v2", {"parser_core_routing.txt": "V2 CORE",
                          "parser_agent.txt": "no placeholder here",
                          "multi_parser_agent.txt": "none here either"})
    with pytest.raises(pv.VariantError, match="parser_core_routing.txt"):
        pv.apply_variant(config, "v2", variants_dir=root)


def test_config_composes_the_default_parser_prompts_with_the_shared_function():
    """ChatConfig and the variant loader compose through one function, so they cannot drift apart."""
    stub = type("Stub", (), {})()
    stub.PROMPTS_DIR = str(PACKAGE / "prompts")
    stub._load_prompt = lambda name: ChatConfig._load_prompt(stub, name)
    stub.PARSER_CORE_ROUTING_PROMPT = stub._load_prompt("parser_core_routing.txt")
    for name in ("parser_agent.txt", "multi_parser_agent.txt"):
        raw = (PACKAGE / "prompts" / name).read_text(encoding="utf-8")
        assert pv.PARSER_CORE_PLACEHOLDER in raw
        assert ChatConfig._load_composed_parser_prompt(stub, name) == compose_parser_prompt(
            raw, stub.PARSER_CORE_ROUTING_PROMPT)


# --------------------------------------------------------------------------- the graph agent addendum


def test_the_addendum_is_appended_to_the_resolved_graph_prompt(root, config):
    _variant(root, "v2", {"graph_agent.txt": "V2 GRAPH\n\n"})
    _variant(root, "v2_apoc", {"apoc.txt": "USE APOC\n"},
             manifest={"inherits": "v2", "graph_agent_addendum": "apoc.txt"})

    out = pv.apply_variant(config, "v2_apoc", variants_dir=root)

    assert out.GRAPH_AGENT_SYSTEM_PROMPT == "V2 GRAPH\n\nUSE APOC\n"
    assert out.PROMPT_VARIANT_FILES["graph_agent_addendum"] == "v2_apoc/apoc.txt"


def test_the_addendum_goes_on_the_default_graph_prompt_when_nothing_replaces_it(root, config):
    _variant(root, "v2_apoc", {"extra.txt": "EXTRA"}, manifest={"graph_agent_addendum": "extra.txt"})

    out = pv.apply_variant(config, "v2_apoc", variants_dir=root)

    assert out.GRAPH_AGENT_SYSTEM_PROMPT == "DEFAULT GRAPH PROMPT\n\nEXTRA"


def test_an_addendum_is_inherited_with_the_file_of_the_variant_that_declared_it(root, config):
    _variant(root, "v2", {"a.txt": "FROM V2"}, manifest={"graph_agent_addendum": "a.txt"})
    _variant(root, "v2_apoc", {"graph_agent.txt": "APOC GRAPH"}, manifest={"inherits": "v2"})

    out = pv.apply_variant(config, "v2_apoc", variants_dir=root)

    assert out.GRAPH_AGENT_SYSTEM_PROMPT == "APOC GRAPH\n\nFROM V2"


@pytest.mark.parametrize("addendum, match", [
    ("missing.txt", "missing.txt"),
    ("../graph_agent.txt", "plain file name"),
    ("sub/a.txt", "plain file name"),
    ("graph_agent.txt", "graph_agent.txt"),
    ("variant.json", "variant.json"),
])
def test_a_bad_addendum_is_refused(root, addendum, match):
    _variant(root, "v2", {"graph_agent.txt": "G"}, manifest={"graph_agent_addendum": addendum})
    with pytest.raises(pv.VariantError, match=match):
        pv.load_variant("v2", variants_dir=root)


# --------------------------------------------------------------------------- settings


def test_the_defaults_when_variant_json_is_absent(root, config):
    _variant(root, "v2", {"graph_agent.txt": "G"})

    v = pv.load_variant("v2", variants_dir=root)
    out = pv.apply_variant(config, "v2", variants_dir=root)

    assert (v.description, v.inherits, v.project_parser_plan, v.allowed_procedures) == ("", None, False, ())
    assert out.PROJECT_PARSER_PLAN is False
    assert out.EXTRA_ALLOWED_PROCEDURES == frozenset()


def test_a_child_inherits_the_settings_it_does_not_state(root, config):
    _variant(root, "v2", manifest={"description": "v2", "project_parser_plan": True})
    _variant(root, "v2_apoc", manifest={"description": "apoc", "inherits": "v2",
                                         "allowed_procedures": ["apoc.path.subgraphNodes"]})

    out = pv.apply_variant(config, "v2_apoc", variants_dir=root)

    assert out.PROJECT_PARSER_PLAN is True
    assert out.EXTRA_ALLOWED_PROCEDURES == frozenset({"apoc.path.subgraphNodes"})
    assert pv.load_variant("v2_apoc", variants_dir=root).description == "apoc"


def test_a_child_overrides_the_settings_it_states(root, config):
    _variant(root, "v2", manifest={"project_parser_plan": True, "allowed_procedures": ["apoc.path.expand"]})
    _variant(root, "v2_apoc", manifest={"inherits": "v2", "project_parser_plan": False,
                                         "allowed_procedures": []})

    out = pv.apply_variant(config, "v2_apoc", variants_dir=root)

    assert out.PROJECT_PARSER_PLAN is False
    assert out.EXTRA_ALLOWED_PROCEDURES == frozenset()


@pytest.mark.parametrize("name", [
    "apoc.path.subgraphNodes", "apoc.path.expandConfig", "apoc.algo.allSimplePaths", "apoc.meta.schema",
])
def test_a_read_procedure_may_be_allowed(root, name):
    _variant(root, "v2_apoc", manifest={"allowed_procedures": [name]})
    assert pv.load_variant("v2_apoc", variants_dir=root).allowed_procedures == (name,)


@pytest.mark.parametrize("name", [
    "subgraphNodes",                      # not fully qualified
    "apoc.path.subgraphNodes()",          # not a name
    "apoc..path",
    "apoc.cypher.run",                    # runs Cypher from a string the text check cannot see
    "apoc.cypher.doIt",
    "apoc.periodic.iterate",              # opens its own transactions
    "apoc.do.when",
    "apoc.when",
    "apoc.load.json",                     # file and network I/O
    "apoc.export.csv.all",
    "apoc.create.node",                   # writes
    "apoc.refactor.mergeNodes",
    "apoc.util.sleep",
    "dbms.listConfig",                    # administration
    "db.createLabel",
])
def test_a_procedure_that_writes_runs_text_or_does_io_is_refused(root, name):
    _variant(root, "v2_apoc", manifest={"allowed_procedures": [name]})
    with pytest.raises(pv.VariantError, match="allowed_procedures"):
        pv.load_variant("v2_apoc", variants_dir=root)


# --------------------------------------------------------------------------- the copy and the record


def test_the_singleton_is_never_mutated(root, config):
    _variant(root, "v2", {"graph_agent.txt": "V2 GRAPH", "parser_core_routing.txt": "CORE",
                          "min_graph_schema.json": {"v": 2}, "min_api_endpoints_enriched.json": []},
             manifest={"project_parser_plan": True, "allowed_procedures": ["apoc.path.expand"]})
    before = _state(config)

    out = pv.apply_variant(config, "v2", variants_dir=root)

    assert out is not config and type(out) is type(config)
    assert _state(config) == before
    assert out.shared is config.shared, "a shallow copy: untouched attributes are the same objects"


def test_the_copy_records_what_ran(root, config):
    _variant(root, "v2", {"api_agent.txt": "A"}, manifest={"project_parser_plan": True})

    out = pv.apply_variant(config, "v2", variants_dir=root)

    assert out.PROMPT_VARIANT == "v2"
    assert out.PROJECT_PARSER_PLAN is True
    assert pv.variant_record(out) == {"prompt_variant": "v2",
                                      "prompt_variant_files": {"api_agent.txt": "v2/api_agent.txt"}}


def test_the_record_of_a_default_config_is_empty(config):
    assert pv.variant_record(config) == {"prompt_variant": None, "prompt_variant_files": None}


def test_the_record_ignores_a_mock_config():
    """Graph and orchestrator tests hand in a MagicMock config, whose every attribute exists."""
    from unittest.mock import MagicMock
    assert pv.variant_record(MagicMock()) == {"prompt_variant": None, "prompt_variant_files": None}
