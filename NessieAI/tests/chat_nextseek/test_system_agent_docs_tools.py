"""The system agent looks up the user docs and the curated catalogs with tools.

A how-to question ("How do I upload samples?") was answered from the capability notes alone, with no
upload page, no workbook, no validation step and an invented administrator (dev launch 2026-10-01): the
agent never saw the user docs, and it was handed every catalog row in full on every call instead. It now
runs a small tool loop: read_doc over the docs folder, get_catalog_entry and list_catalog over the
catalogs, and answer. A docs link is added in code for each page it cited AND read this turn, never for
one it only named. Every page, sample type, assay, project and lab here is invented.
"""
from __future__ import annotations

import copy
import json
from unittest.mock import MagicMock

import pytest

from chat_nextseek import system_tools
from chat_nextseek.agents import system as system_mod
from chat_nextseek.llm_clients import LLMFatalError
from chat_nextseek.schemas import ParserPlan

README = """# Made-up docs

## Working with data

- [Loading records](loading.md)
- [Finding records](finding.md)

## Reference

- [Counts](counts.md)
"""

PAGES = {
    "loading.md": (
        "# Loading records\n\nIntro text.\n\n## Where to load\n\nOpen the Ledger page.\n\n"
        "## The ledger sheet\n\nTwo layouts exist.\n\n### Wide layout\n\nOne row per record.\n\n"
        "## Check it first\n\nPress Verify before you load.\n"
    ),
    "finding.md": "# Finding records\n\n## Export a table\n\nPress Export on the results table.\n",
    "counts.md": "# Counts\n\n## Whole store\n\n12 records on a given day.\n",
}


@pytest.fixture
def docs_dir(tmp_path):
    (tmp_path / "README.md").write_text(README)
    for name, text in PAGES.items():
        (tmp_path / name).write_text(text)
    return tmp_path


SAMPLETYPES = [
    {"SampleType": "ZRC", "Name": "Zircon Source", "Clade": "Source", "Parent_SampleTypes": None},
    {"SampleType": "D.ZQ", "Name": "Zircon Quantity Data", "Clade": "Raw", "Parent_SampleTypes": "ZTP"},
    {"SampleType": "D.YLM", "Name": "Yellow Lumen Data", "Clade": "Raw", "Parent_SampleTypes": "ZRC"},
    {"SampleType": "A.ZQ", "Name": "Zircon Quantity Analysis", "Clade": "Analyzed", "Parent_SampleTypes": "D.ZQ"},
]
ASSAYS = [
    {"Name": "Zircon Counting", "Alternative Assay Names": "ZC, zircon tally"},
    {"Name": "Lumen Imaging", "Alternative Assay Names": None},
    {"Name": "Lumen Imaging Analysis", "Alternative Assay Names": "LIA"},
]
PROJECT = {"name": "Quill", "alternative_names": ["Quill Program"], "entity_type": "project", "project_id": 3}
INVESTIGATION = {"name": "Quill Core", "alternative_names": [], "entity_type": "investigation",
                 "project_id": 3, "parent_project": "Quill"}
LABS = [{"code": "QXL", "name": "Quintana", "title": "Quintana Lab", "project_ids": [3]}]


def _config(docs_dir=None):
    c = MagicMock()
    c.DOCS_DIR = str(docs_dir) if docs_dir else None
    c.FULL_SAMPLETYPES = SAMPLETYPES
    c.FULL_ASSAYS = ASSAYS
    c.FULL_SAMPLETYPES_MAP = {r["SampleType"]: r for r in SAMPLETYPES}
    c.FULL_ASSAYS_MAP = {r["Name"]: r for r in ASSAYS}
    c.FULL_PROJECTS_MAP = {"Quill": PROJECT}
    c.FULL_INVESTIGATIONS_MAP = {"Quill Core": INVESTIGATION}
    c.LABS = LABS
    c.MIN_API_ENDPOINTS = []
    c.CAPABILITIES_DOC = "caps"
    c.NEO4J_SCHEMA = {}
    c.SYSTEM_AGENT_SYSTEM_PROMPT = "system prompt"
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


# --- the docs ---------------------------------------------------------------------------------------------------------


def test_the_docs_index_lists_each_page_in_readme_order_with_its_address_and_headings(docs_dir):
    pages = system_tools.load_docs(docs_dir)
    assert list(pages) == ["loading", "finding", "counts"]
    index = system_tools.docs_index(pages)
    assert "Loading records: /docs/loading/" in index
    assert "Where to load; The ledger sheet; Wide layout; Check it first" in index
    assert index.index("loading") < index.index("finding") < index.index("counts")


def test_no_docs_folder_gives_an_empty_index_and_read_doc_says_so(tmp_path):
    pages = system_tools.load_docs(tmp_path / "missing")
    assert pages == {}
    assert system_tools.read_doc(pages, "loading")["ok"] is False
    assert system_tools.load_docs(MagicMock()) == {}  # a config that has no DOCS_DIR


def test_read_doc_returns_one_section_with_its_subsections(docs_dir):
    pages = system_tools.load_docs(docs_dir)
    out = system_tools.read_doc(pages, "loading", "the ledger sheet")
    assert out["ok"] and out["url"] == "/docs/loading/"
    assert "Two layouts exist." in out["text"] and "One row per record." in out["text"]
    assert "Press Verify" not in out["text"]


def test_read_doc_on_an_unknown_slug_or_heading_names_what_exists(docs_dir):
    pages = system_tools.load_docs(docs_dir)
    miss = system_tools.read_doc(pages, "uploading")
    assert miss["ok"] is False and miss["slugs"] == ["loading", "finding", "counts"]
    no_heading = system_tools.read_doc(pages, "finding", "Delete a table")
    assert no_heading["ok"] is False and no_heading["headings"] == ["Export a table"]


# --- the catalogs -----------------------------------------------------------------------------------------------------


def test_list_catalog_counts_in_code_by_clade():
    out = system_tools.list_catalog(_config(), "sample_type", clade="raw")
    assert out["count"] == 2
    assert {r["code"] for r in out["rows"]} == {"D.ZQ", "D.YLM"}


def test_list_catalog_counts_assays_by_what_their_names_contain():
    out = system_tools.list_catalog(_config(), "assay", contains="lumen")
    assert out["count"] == 2
    assert [r["name"] for r in out["rows"]] == ["Lumen Imaging", "Lumen Imaging Analysis"]


def test_get_catalog_entry_by_code_and_by_alternative_name():
    config = _config()
    assert system_tools.get_catalog_entry(config, "sample_type", "d.zq")["row"]["Name"] == "Zircon Quantity Data"
    assert system_tools.get_catalog_entry(config, "assay", "zircon tally")["row"]["Name"] == "Zircon Counting"
    assert system_tools.get_catalog_entry(config, "project", "quill program")["row"] == PROJECT
    assert system_tools.get_catalog_entry(config, "investigation", "Quill Core")["row"] == INVESTIGATION
    assert system_tools.get_catalog_entry(config, "lab", "qxl")["row"]["name"] == "Quintana"


def test_a_catalog_miss_offers_near_names():
    out = system_tools.get_catalog_entry(_config(), "assay", "Lumen Imagin")
    assert out["ok"] is False
    assert "Lumen Imaging" in out["near"]


def test_the_catalog_index_names_every_row_once():
    index = system_tools.catalog_index(_config())
    for row in SAMPLETYPES:
        assert index.count(f"{row['SampleType']} {row['Name']}") == 1
    for row in ASSAYS:
        assert f"- {row['Name']}\n" in index + "\n"


# --- the loop ---------------------------------------------------------------------------------------------------------


def _tool(name, **args):
    return {"type": "tool_use", "id": f"t-{name}-{len(args)}", "name": name, "input": args}


class _Script:
    """Stands in for tool_loop.call_tools: replays one model turn per call and records what it was sent."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls = []

    def __call__(self, config, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))  # the loop keeps appending to the list it sent
        return {"stop_reason": "tool_use", "content": self.turns.pop(0)}


def _run(monkeypatch, config, script, question="How do I load records?"):
    monkeypatch.setattr(system_mod, "call_tools", script)
    monkeypatch.setattr(system_mod, "live_catalog_context", lambda *a, **k: None)
    monkeypatch.setattr(system_mod.graph_catalog, "committed_schema", lambda config: {})
    return system_mod.system_agent(config, question, {}, ParserPlan(mode="system_question"))


def test_a_how_to_question_reads_its_page_and_the_reply_links_it(monkeypatch, docs_dir):
    """The counter-example's shape: a how-to question about loading data."""
    script = _Script(
        [_tool("read_doc", slug="loading")],
        [_tool("answer", mode="get_capabilities", narrative="Open the Ledger page, then press Verify.",
               docs_cited=["loading"])],
    )
    out = _run(monkeypatch, _config(docs_dir), script)
    assert out.narrative.startswith("Open the Ledger page, then press Verify.")
    assert out.narrative.rstrip().endswith("See: [Loading records](/docs/loading/)")
    assert out.docs_cited == ["loading"]
    assert "DOCS_INDEX" in script.calls[0]["system"]
    sent = json.loads(script.calls[1]["messages"][-1]["content"][0]["content"])
    assert "Press Verify before you load." in sent["text"]


def test_a_how_to_question_on_another_page_links_that_page(monkeypatch, docs_dir):
    """A sibling: a different page and a different kind of task (exporting results)."""
    script = _Script(
        [_tool("read_doc", slug="finding", heading="Export a table")],
        [_tool("answer", mode="get_capabilities", narrative="Press Export.", docs_cited=["finding"])],
    )
    out = _run(monkeypatch, _config(docs_dir), script, "How do I export my results?")
    assert out.narrative.endswith("See: [Finding records](/docs/finding/)")


def test_a_page_cited_but_never_read_gets_no_link(monkeypatch, docs_dir):
    script = _Script(
        [_tool("read_doc", slug="loading")],
        [_tool("answer", mode="get_capabilities", narrative="Press Verify.",
               docs_cited=["loading", "finding", "nowhere"])],
    )
    out = _run(monkeypatch, _config(docs_dir), script)
    assert out.docs_cited == ["loading"]
    assert "/docs/finding/" not in out.narrative and "/docs/nowhere/" not in out.narrative
    assert "finding" in out.notes and "nowhere" in out.notes


def test_a_link_already_in_the_narrative_is_not_added_twice(monkeypatch, docs_dir):
    script = _Script(
        [_tool("read_doc", slug="loading")],
        [_tool("answer", mode="get_capabilities", narrative="See [the page](/docs/loading/).",
               docs_cited=["loading"])],
    )
    out = _run(monkeypatch, _config(docs_dir), script)
    assert out.narrative.count("/docs/loading/") == 1


def test_a_count_question_gets_the_count_list_catalog_computed(monkeypatch, docs_dir):
    script = _Script(
        [_tool("list_catalog", kind="sample_type", clade="Raw")],
        [_tool("answer", mode="get_searches", narrative="There are 2 Raw sample types.")],
    )
    _run(monkeypatch, _config(docs_dir), script, "How many raw sample types are there?")
    sent = json.loads(script.calls[1]["messages"][-1]["content"][0]["content"])
    assert sent["count"] == 2


def test_the_last_pass_offers_only_answer_and_runs_nothing_else(monkeypatch, docs_dir):
    turns = [[_tool("read_doc", slug="loading")] for _ in range(system_mod.MAX_ITER)]
    turns.append([_tool("read_doc", slug="finding"), _tool("answer", mode="get_capabilities", narrative="done")])
    script = _Script(*turns)
    out = _run(monkeypatch, _config(docs_dir), script)
    assert [t["name"] for t in script.calls[-1]["tools"]] == ["answer"]
    assert out.narrative == "done"
    assert len(script.calls) == system_mod.MAX_ITER + 1


def test_a_model_and_fallback_failure_ends_the_turn(monkeypatch, docs_dir):
    def fatal(config, **kwargs):
        raise LLMFatalError("both failed", unavailable=True)

    monkeypatch.setattr(system_mod, "call_tools", fatal)
    monkeypatch.setattr(system_mod, "live_catalog_context", lambda *a, **k: None)
    with pytest.raises(LLMFatalError):
        system_mod.system_agent(_config(docs_dir), "q", {}, ParserPlan(mode="system_question"))


def test_the_loop_passes_the_system_catalog_key(monkeypatch, docs_dir):
    script = _Script([_tool("answer", mode="get_capabilities", narrative="ok")])
    _run(monkeypatch, _config(docs_dir), script)
    assert script.calls[0]["agent_label"] == "system"


# --- the model catalog ------------------------------------------------------------------------------------------------


def test_the_default_profile_runs_the_system_agent_on_a_tool_capable_model_with_a_fallback():
    from pathlib import Path

    path = Path(system_mod.__file__).resolve().parents[3] / "agent_model_catalog.json"
    catalog = json.loads(path.read_text())
    models = catalog["default"]["models"]
    owners = [m for m, rows in models.items()
              for row in (rows if isinstance(rows, list) else [rows]) if "system" in row["agents"]]
    assert owners == ["global.anthropic.claude-sonnet-5-5"]
    assert catalog["_fallback"]["system"]["model"] == "us.anthropic.claude-opus-5-5"
    # Tools need a Bedrock client (BedrockClient.chat_with_tools), in every profile.
    for profile in ("gcp:current", "gcp:lite", "anth:current", "anth:lite"):
        rows = [row for m, rs in catalog[profile]["models"].items()
                for row in (rs if isinstance(rs, list) else [rs])]
        system_row = next(row for row in rows if "system" in row["agents"])
        assert system_row["provider"] == "anth", profile


# --- review fixes (F9) ------------------------------------------------------------------------------------------------

CANNED = "I encountered an issue answering your question."


def test_an_empty_narrative_from_answer_is_a_failure_with_the_canned_output(monkeypatch, docs_dir):
    script = _Script([_tool("answer", mode="get_capabilities", narrative="   ")])
    out = _run(monkeypatch, _config(docs_dir), script)
    assert out.narrative.startswith(CANNED) and out.notes.startswith("error:")


def test_a_non_empty_narrative_from_answer_is_kept(monkeypatch, docs_dir):
    """Control: the same call with text is not the canned output."""
    script = _Script([_tool("answer", mode="get_capabilities", narrative="Press Verify.")])
    assert _run(monkeypatch, _config(docs_dir), script).narrative == "Press Verify."


def test_a_page_that_is_not_utf8_is_skipped_not_fatal(tmp_path):
    (tmp_path / "README.md").write_text(README)
    (tmp_path / "loading.md").write_bytes(b"# Loading\n\xff\xfe broken")
    (tmp_path / "finding.md").write_text(PAGES["finding.md"])
    assert list(system_tools.load_docs(str(tmp_path))) == ["finding"]


def test_a_readme_that_is_not_utf8_gives_no_pages(tmp_path):
    (tmp_path / "README.md").write_bytes(b"\xff\xfe")
    assert system_tools.load_docs(str(tmp_path)) == {}


def test_prose_without_the_answer_tool_loses_a_link_to_a_page_not_read(monkeypatch, docs_dir):
    script = _Script(
        [_tool("read_doc", slug="loading")],
        [{"type": "text", "text": "Use [the loader](/docs/loading/) or [the export page](/docs/finding/#x)."}],
    )
    out = _run(monkeypatch, _config(docs_dir), script)
    assert "[the loader](/docs/loading/)" in out.narrative
    assert "/docs/finding/" not in out.narrative and "the export page" in out.narrative


def test_prose_naming_a_page_never_read_loses_the_link_when_nothing_was_read(monkeypatch, docs_dir):
    """Another prose answer of the same kind: no page read at all."""
    script = _Script([{"type": "text", "text": "See [Counts](/docs/counts/) for totals."}])
    out = _run(monkeypatch, _config(docs_dir), script)
    assert out.narrative == "See Counts for totals."


def test_a_client_with_no_tool_surface_gets_the_canned_failure_before_any_call(monkeypatch, docs_dir):
    config = _config(docs_dir)
    config.get_agent_model.return_value = (object(), "model", None)
    script = _Script()
    out = _run(monkeypatch, config, script)
    assert out.narrative.startswith(CANNED) and script.calls == []
