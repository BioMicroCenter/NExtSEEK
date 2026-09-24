"""A project the entity step resolved is scoped by IN_PROJECT on its exact title (P1).

"What species are the samples in the IMPACT project?" answered 897 where the truth is 892,
the IMPAcTb samples with a Species value (local 2026-09-22, P1 in the dev-box batches, and
the production runs of 2026-09-23). The graph agent matched 'impact' by CONTAINS across Study
and Investigation titles as well as the Project, and a MetNet paper titled "Impact of
fibrinogen, fibrin thrombi and thrombin on cancer cell extravasation ..." added its 5 Homo
sapiens samples. The entity step resolves the catalog name "Impact"; the graph stores the
title "IMPAcTb", which is one of that catalog row's alternative names and which the graph
agent was never shown. It is now handed the title, told to scope on it exactly, and the
reply's scope check reads the title as the project the user named.

Checked read-only against the local graph: ``MATCH (s:Sample)-[:IN_PROJECT]->(p:Project)
WHERE p.title = 'IMPAcTb' AND s.Species IS NOT NULL`` counts 892, the CONTAINS form 897.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import chat_nextseek
from chat_nextseek import graph_catalog as gcat
from chat_nextseek import graph_context as gctx
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.context_rows import is_project_row
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.helpers.query_scope import describe_query_scope
from chat_nextseek.schemas import GraphAgentPlan
from chat_nextseek.schemas.entity import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan

PACKAGE = Path(chat_nextseek.__file__).resolve().parent
PROJECT_ROWS = [row for row in json.loads((PACKAGE / "context" / "projects_db.json").read_text(encoding="utf-8"))
                if is_project_row(row)]
# The Project titles of the graph (local, 2026-09-23).
GRAPH_TITLES = ("BPRC", "Breakthrough_Cancer", "CGR-Endo", "Cancer_Systems_Biology_Consortium(CSBC)", "IMPAcTb",
                "MIT-Koch", "MIT_SRP", "MetNet", "NAMs", "RMS-NGC", "Shoulders Independent Projects", "TCGA")


# --- the catalog names the title --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, title", [
    ("Impact", "IMPAcTb"),                           # an alternative name of the catalog row
    ("IMPACT", "IMPAcTb"),
    ("SRP", "MIT_SRP"),                              # "MIT SRP" folds to the title
    ("Shoulders", "Shoulders Independent Projects"),
    ("Griffith", "CGR-Endo"),
    ("TCGA", "TCGA"),                                # the title itself
])
def test_a_resolved_project_maps_to_its_stored_title(name, title):
    assert gctx.project_titles_for([name], PROJECT_ROWS, GRAPH_TITLES) == {name: title}


def test_an_investigation_row_never_names_a_title():
    """"Impactb Investigation" carries IMPAcTb among its names; it is not a project row, so it maps nothing."""
    rows = [{"name": "Impactb Investigation", "alternative_names": ["Impact", "IMPAcTb"],
             "entity_type": "investigation", "parent_project": "Impact"}]
    assert not any(is_project_row(r) for r in rows)
    assert gctx.project_titles_for(["Impactb Investigation", "Collagen Study"], PROJECT_ROWS, GRAPH_TITLES) == {}


def test_a_title_the_caller_cannot_see_is_never_named():
    assert gctx.project_titles_for(["Impact"], PROJECT_ROWS, ("MIT_SRP",)) == {}


def test_an_ambiguous_name_is_left_out():
    rows = [{"name": "Twin", "alternative_names": ["Alpha"]}, {"name": "Twin B", "alternative_names": ["Twin", "Beta"]}]
    assert gctx.project_titles_for(["Twin"], rows, ("Alpha", "Beta")) == {}


def test_the_block_names_each_title_exactly():
    block = gctx.render_project_titles({"Impact": "IMPAcTb"})

    assert block.startswith("PROJECTS NAMED IN THIS QUESTION")
    assert '- "Impact" is the project titled "IMPAcTb"' in block
    assert gctx.render_project_titles({}) == ""


# --- the graph agent is handed the title and records it -----------------------------------------------------------------


SNAPSHOT = gcat.CatalogSnapshot(catalog_hash="h1", synced_at=None, has_usage=False, index=(),
                                guard={"T_PAT": frozenset({"Species"})})
VOCAB = gcat.Vocabulary(investigation_titles=("Impact", "Impactb Investigation"), project_titles=GRAPH_TITLES,
                        study_titles=(), published_studies=(), assay_titles=(), protocol_titles=(),
                        assay_connections=())
CYPHER = ("MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE p.title = $project_title AND s.Species IS NOT NULL "
          "RETURN s.Species AS species, count(*) AS n ORDER BY n DESC")


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(gcat, "get_snapshot", lambda config: SNAPSHOT)
    monkeypatch.setattr(gcat, "get_type_details", lambda config, titles: [])
    monkeypatch.setattr(gcat, "get_vocabulary", lambda config: VOCAB)


def _config():
    c = MagicMock()
    c.GRAPH_SCOPE = GraphScope.admin("test")
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.FULL_PROJECTS_MAP = {row["name"]: row for row in PROJECT_ROWS}
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def test_the_graph_agent_is_told_the_title_and_the_plan_records_it(monkeypatch, live):
    calls = []

    def llm(**kwargs):
        calls.append(kwargs)
        return GraphAgentPlan(cypher=CYPHER, explanation="x", parameters={"project_title": "IMPAcTb"})

    monkeypatch.setattr(graph_mod, "call_llm_structured", llm)
    plan = ParserPlan(mode="graph_query").model_dump()
    plan["resolved"] = {"projects": ["Impact"]}
    out = graph_mod.graph_agent(_config(), "What species are the samples in the IMPACT project?",
                                {"projects": ["Impact"]}, plan)

    blob = "\n".join(m["content"] for m in calls[0]["messages"])
    assert '- "Impact" is the project titled "IMPAcTb"' in blob
    assert out.project_titles == {"Impact": "IMPAcTb"}


def test_no_resolved_project_adds_no_block(monkeypatch, live):
    calls = []

    def llm(**kwargs):
        calls.append(kwargs)
        return GraphAgentPlan(cypher="MATCH (s:T_TIS) RETURN count(s) AS n", explanation="x", parameters={})

    monkeypatch.setattr(graph_mod, "call_llm_structured", llm)
    out = graph_mod.graph_agent(_config(), "how many tissues", {}, None)

    assert "PROJECTS NAMED IN THIS QUESTION" not in "\n".join(m["content"] for m in calls[0]["messages"])
    assert out.project_titles == {}


def test_the_title_map_is_not_in_the_models_schema():
    assert "project_titles" not in GraphAgentPlan.model_json_schema().get("properties", {})
    assert GraphAgentPlan(cypher="x").project_titles == {}


# --- the reply's scope check reads the title as the named project -------------------------------------------------------


def _scope(project_titles, parameters=None):
    graph_plan = {"cypher": CYPHER, "parameters": parameters or {"project_title": "IMPAcTb"}}
    if project_titles is not None:
        graph_plan["project_titles"] = project_titles
    return describe_query_scope(
        entity_result=EntityAgentOutput(projects=["Impact"], keywords=["Impact"]).model_dump(),
        parser_plan=ParserPlan(mode="graph_query").model_dump(),
        graph_plan=graph_plan,
        user_query="What species are the samples in the IMPACT project?",
    )


def test_without_the_title_map_an_exact_title_scope_reads_as_dropped():
    """What the reply would have been told: IMPAcTb is not the word Impact."""
    assert "project Impact" in _scope(None).not_applied


def test_a_query_on_the_stored_title_applies_the_project_and_its_keyword():
    scope = _scope({"Impact": "IMPAcTb"})

    assert not scope.not_applied, scope.not_applied
    assert "project Impact" in scope.applied and 'keyword "Impact"' in scope.applied


def test_a_mapped_title_the_query_does_not_carry_changes_nothing():
    scope = _scope({"Impact": "IMPAcTb"}, parameters={"project_title": "MIT_SRP"})

    assert "project Impact" in scope.not_applied


# --- the prompt ---------------------------------------------------------------------------------------------------------


PROMPT = (PACKAGE / "prompts" / "graph_agent.txt").read_text(encoding="utf-8")
STEP5 = PROMPT[PROMPT.index("## STEP 5"):PROMPT.index("An investigation marked not on every instance")]


def test_step5_scopes_a_resolved_project_on_its_exact_title():
    rule = STEP5[STEP5.index("A project the entity step resolved"):]

    assert "EXACT title" in rule
    assert re.search(r"MATCH \(s:Sample\)-\[:IN_PROJECT\]->\(p:Project\)\s*WHERE p\.title = \$project_title", rule)
    assert "PROJECTS NAMED IN THIS QUESTION" in rule
    assert '"Impact" is the project titled `IMPAcTb`' in rule
    assert "Never `CONTAINS` on a project's name" in rule
    assert "never OR a Study or Investigation title match beside it" in rule
    assert "892" in rule and "Impact of fibrinogen" in rule


def test_the_study_and_investigation_form_is_kept_for_a_name_that_is_no_project_title():
    project_rule = STEP5.index("A project the entity step resolved")
    other = STEP5.index("a name that is no project title")

    assert project_rule < other, "the project rule is read first"
    rest = STEP5[other:]
    assert "toLower(st.title) CONTAINS toLower($project)" in rest
    assert "toLower(inv.title) CONTAINS toLower($project)" in rest
    assert "OPTIONAL MATCH" in rest


def test_the_tuned_scope_behaviours_stand():
    """Shoulders (an investigation whose project holds 568) and the no-search_text-beside-a-scope rule."""
    assert "A NAME YOU WERE GIVEN in PROJECT TITLES, INVESTIGATION TITLES or STUDY TITLES is a scope" in STEP5
    assert "its project held 568 samples" in STEP5
    assert "try the other two of Project title, Investigation title and Study title" in STEP5
    assert '"Shoulders" is `Shoulders Independent Projects`' in STEP5


@pytest.mark.parametrize("name", ["PUBLISHED", "Published Data", "Published"])
def test_published_data_maps_to_project_6_s_stored_title(name):
    """Fix 7 item 1 (operator 2026-09-24): PUBLISHED is project 6, whose stored title is "Training/Test"
    (it holds the already-published data), so "published data" scopes by IN_PROJECT on it."""
    titles = (*GRAPH_TITLES, "Training/Test")
    assert gctx.project_titles_for([name], PROJECT_ROWS, titles) == {name: "Training/Test"}
