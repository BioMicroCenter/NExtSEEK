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


def test_an_investigation_row_names_only_a_title_its_graph_holds():
    """"Impactb Investigation" carries IMPAcTb among its names: with no Investigation title in the graph that
    reaches only the Project, as a broader container (own False); a name no row or title holds maps nothing."""
    rows = [{"name": "Impactb Investigation", "alternative_names": ["Impact", "IMPAcTb"],
             "entity_type": "investigation", "parent_project": "Impact"}]
    assert not any(is_project_row(r) for r in rows)
    assert gctx.project_titles_for(["Collagen Study"], PROJECT_ROWS, GRAPH_TITLES) == {}


# --- A1: a name resolves to the container titles that exist, narrowest first -------------------------------------------

INV_ROW = {"name": "Alpha Cohort", "alternative_names": ["Gamma Lab"], "entity_type": "investigation",
           "parent_project": "Gamma Group"}
INV_ROW_2 = {"name": "Delta Trial", "alternative_names": ["Epsilon"], "entity_type": "investigation",
             "parent_project": "Epsilon Group"}
PROJ_ROW = {"name": "Gamma Group", "alternative_names": ["Gamma Lab"], "entity_type": "project"}


def _levels(inv=(), proj=(), study=()):
    return {"study": study, "investigation": inv, "project": proj}


def test_an_investigation_name_reaches_the_investigation_title_this_graph_holds():
    """The catalog name is not a title here; its alternative name is an Investigation title (the box differs). An
    investigation row's alternative names can be its owner's names, so that title is used but is not its own scope."""
    hits = gctx.container_titles_for(["Alpha Cohort"], [INV_ROW, PROJ_ROW], _levels(inv=("Gamma Lab",), proj=("Gamma Group",)))
    assert hits == {"Alpha Cohort": ("investigation", "Gamma Lab", False)}


def test_a_second_investigation_name_with_another_box_title():
    hits = gctx.container_titles_for(["Delta Trial"], [INV_ROW_2], _levels(inv=("Epsilon",)))
    assert hits == {"Delta Trial": ("investigation", "Epsilon", False)}


def test_an_investigation_titled_with_the_rows_own_name_is_its_own_scope():
    hits = gctx.container_titles_for(["Gamma Lab"], [INV_ROW], _levels(inv=("Alpha Cohort",)))
    assert hits == {"Gamma Lab": ("investigation", "Alpha Cohort", True)}


def test_a_title_that_is_the_name_itself_beats_an_alternative_name():
    """Both investigations exist (production's shape): the one the user named is used, not the owner's alias."""
    hits = gctx.container_titles_for(["Alpha Cohort"], [INV_ROW], _levels(inv=("Alpha Cohort", "Gamma Lab")))
    assert hits == {"Alpha Cohort": ("investigation", "Alpha Cohort", True)}


def test_only_a_broader_container_is_used_but_not_counted_as_the_name():
    hits = gctx.container_titles_for(["Alpha Cohort"], [INV_ROW], _levels(proj=("Gamma Group",)))
    assert hits == {"Alpha Cohort": ("project", "Gamma Group", False)}
    other = gctx.container_titles_for(["Delta Trial"], [INV_ROW_2], _levels(proj=("Epsilon Group",)))
    assert other == {"Delta Trial": ("project", "Epsilon Group", False)}


def test_two_titles_at_the_narrowest_level_stop_the_search():
    rows = [{"name": "Alpha Cohort", "alternative_names": ["Gamma Lab", "Gamma Two"], "entity_type": "investigation",
             "parent_project": "Gamma Group"}]
    assert gctx.container_titles_for(["Alpha Cohort"], rows, _levels(inv=("Gamma Lab", "Gamma Two"),
                                                                    proj=("Gamma Group",))) == {}


def test_a_name_with_no_catalog_row_maps_to_a_study_title_that_exists():
    hits = gctx.container_titles_for(["Zed Paper"], [], _levels(study=("Zed-Paper",), proj=("Zed Paper",)))
    assert hits == {"Zed Paper": ("study", "Zed-Paper", True)}


# --- the real catalog shape: an investigation row lists its owner's names (made-up rows, projects_db.json's shape) ------

KAPPA_PROJECT = {"name": "Kappa", "alternative_names": ["KAPPA", "KappaTx", "Kappa-Tx"], "entity_type": "project"}
KAPPA_INV = {"name": "Kappatx Investigation", "alternative_names": ["Kappa", "KAPPA", "KappaTx"],
             "entity_type": "investigation", "parent_project": "Kappa"}
LUMEN_PROJECT = {"name": "Lumen", "alternative_names": ["LUM-Endo", "Lumen Lab"], "entity_type": "project"}
LUMEN_INV = {"name": "Ovaria", "alternative_names": ["Lumen", "LUM-Endo"], "entity_type": "investigation",
             "parent_project": "Lumen"}
RHO_PROJECT = {"name": "Rho", "alternative_names": ["Rhodes", "MIT Rho", "Rho Program"], "entity_type": "project"}
RHO_INV = {"name": "MIT_Rho", "alternative_names": ["Rho", "MIT Rho", "Rho Program"], "entity_type": "investigation",
           "parent_project": "Rho"}
SHAPE_ROWS = [KAPPA_PROJECT, KAPPA_INV, LUMEN_PROJECT, LUMEN_INV, RHO_PROJECT, RHO_INV]
SHAPE_BOX = _levels(proj=("KAPPAtx", "LUM-Endo", "MIT_Rho"), inv=("Kappatx Investigation", "Ovaria", "MIT_Rho"))
# The same box also holding a test project whose investigations are titled like the real projects' catalog names.
SHAPE_BOX_WITH_TEST_PROJECT = _levels(proj=("KAPPAtx", "LUM-Endo", "MIT_Rho", "Sandbox_Project"),
                                      inv=("Kappatx Investigation", "Ovaria", "MIT_Rho", "Kappa", "Lumen", "Rho"))
SHAPE_BOXES = pytest.mark.parametrize("box", [SHAPE_BOX, SHAPE_BOX_WITH_TEST_PROJECT], ids=["box", "with-test-project"])


@SHAPE_BOXES
@pytest.mark.parametrize("name, title", [("Kappa", "KAPPAtx"), ("KAPPA", "KAPPAtx"), ("Lumen", "LUM-Endo"),
                                         ("Rho", "MIT_Rho")])
def test_a_project_name_its_investigation_row_also_carries_resolves_to_the_project(box, name, title):
    """A matched project row makes the name a project: its investigation row's copy of the name, or a test
    project's investigation titled with it, never takes the question to an investigation."""
    assert gctx.container_titles_for([name], SHAPE_ROWS, box) == {name: ("project", title, True)}


@SHAPE_BOXES
@pytest.mark.parametrize("name", ["Rho Program", "Rhodes", "Kappa-Tx", "Lumen Lab"])
def test_an_alias_a_project_row_holds_resolves_as_the_project_only_view_does(box, name):
    project_rows = [row for row in SHAPE_ROWS if is_project_row(row)]
    expected = gctx.project_titles_for([name], project_rows, box["project"])
    assert expected, name
    hits = gctx.container_titles_for([name], SHAPE_ROWS, box)
    assert hits == {name: ("project", expected[name], True)}


@SHAPE_BOXES
@pytest.mark.parametrize("name, title", [("Kappatx Investigation", "Kappatx Investigation"), ("Ovaria", "Ovaria")])
def test_an_investigation_only_name_still_reaches_its_own_investigation(box, name, title):
    assert gctx.container_titles_for([name], SHAPE_ROWS, box) == {name: ("investigation", title, True)}


def test_a_parent_project_name_is_never_tried_as_an_investigation_title():
    """An investigation whose own title is missing never lands on a sibling investigation named like its parent."""
    rows = [{"name": "Sigma Cohort", "alternative_names": [], "entity_type": "investigation", "parent_project": "Tau"}]
    hits = gctx.container_titles_for(["Sigma Cohort"], rows, _levels(inv=("Tau", "Sigma-cohort-2031"), proj=("Tau",)))
    assert hits == {"Sigma Cohort": ("project", "Tau", False)}
    other = [{"name": "Nu Arm", "alternative_names": [], "entity_type": "investigation", "parent_project": "Xi"}]
    assert gctx.container_titles_for(["Nu Arm"], other, _levels(inv=("Xi",))) == {}


def test_a_name_that_is_itself_a_project_title_keeps_it_when_no_investigation_holds_it():
    """An investigation row named like its parent project (one cohort, one project): with no Investigation of that
    title in the graph, the Project titled with the name itself scopes it, as before any other level was read."""
    rows = [{"name": "Tau Atlas", "alternative_names": ["The Tau Atlas"], "entity_type": "investigation",
             "parent_project": "Tau Atlas"}]
    assert gctx.container_titles_for(["Tau Atlas"], rows, _levels(proj=("Tau Atlas",))) == {
        "Tau Atlas": ("project", "Tau Atlas", True)}
    assert gctx.container_titles_for(["Tau Atlas"], rows, _levels(inv=("Tau Atlas",), proj=("Tau Atlas",))) == {
        "Tau Atlas": ("investigation", "Tau Atlas", True)}


def test_the_graph_agent_reads_investigation_rows_and_still_names_the_project(monkeypatch):
    """The live path hands both maps to the resolver: a project name its investigation row also carries, on a box
    that holds a test project's investigation of that name, is still the Project title, and counts as applied."""
    vocab = gcat.Vocabulary(investigation_titles=SHAPE_BOX_WITH_TEST_PROJECT["investigation"],
                            project_titles=SHAPE_BOX_WITH_TEST_PROJECT["project"], study_titles=(),
                            published_studies=(), assay_titles=(), protocol_titles=(), assay_connections=())
    monkeypatch.setattr(gcat, "get_snapshot", lambda config: SNAPSHOT)
    monkeypatch.setattr(gcat, "get_type_details", lambda config, titles: [])
    monkeypatch.setattr(gcat, "get_vocabulary", lambda config: vocab)
    calls = []

    def llm(**kwargs):
        calls.append(kwargs)
        return GraphAgentPlan(cypher=CYPHER, explanation="x", parameters={"project_title": "KAPPAtx"})

    monkeypatch.setattr(graph_mod, "call_llm_structured", llm)
    config = _config()
    config.FULL_PROJECTS_MAP = {row["name"]: row for row in SHAPE_ROWS if is_project_row(row)}
    config.FULL_INVESTIGATIONS_MAP = {row["name"]: row for row in SHAPE_ROWS if not is_project_row(row)}
    plan = ParserPlan(mode="graph_query").model_dump()
    plan["resolved"] = {"projects": ["Kappa"]}
    out = graph_mod.graph_agent(config, "What species are the samples in the Kappa project?", {"projects": ["Kappa"]},
                                plan)

    assert '- "Kappa" is the project titled "KAPPAtx"' in "\n".join(m["content"] for m in calls[0]["messages"])
    assert out.project_titles == {"Kappa": "KAPPAtx"}


def test_the_block_header_names_the_three_levels():
    block = gctx.render_project_titles({"Alpha Cohort": "Gamma Lab"}, {"Alpha Cohort": "investigation"})
    assert block.startswith(
        "PROJECTS NAMED IN THIS QUESTION (the exact title each is stored under and whether it is a project, an "
        "investigation or a study, found through the project catalog's names and alternative names; scope on "
        "this title, STEP 5):")


def test_the_prompt_tells_the_agent_to_scope_on_an_investigation_or_study_title():
    assert ("a PROJECTS NAMED IN THIS QUESTION block after it gives the exact title that project, investigation or "
            "study is stored under (STEP 5).") in PROMPT
    bullet = PROMPT[PROMPT.index("- **A name the PROJECTS NAMED IN THIS QUESTION block gives as an investigation"):]
    bullet = bullet[:bullet.index('- **"Study X"')]
    assert "WHERE inv.title = $investigation_title" in bullet
    assert "(s:Sample)-[:IN_STUDY]->(st:Study)-[:IN_INVESTIGATION]->(inv:Investigation)" in bullet
    assert "`WHERE st.title = $study_title` for a study" in bullet
    assert "The block's title wins over the name as the user wrote it." in bullet
    # the bullet sits after the project bullet and before the "Study X" bullet
    assert PROMPT.index("A project the entity step resolved") < PROMPT.index("A name the PROJECTS NAMED") < PROMPT.index('- **"Study X"')


def test_the_contains_rule_for_other_names_yields_to_a_title_the_block_gives():
    # The "Study X" bullet's case-insensitive match is for names the block resolved to no title; a name the
    # block gives a title for is scoped on that exact title (the bullet above), never matched by CONTAINS.
    bullet = PROMPT[PROMPT.index('- **"Study X"'):]
    assert ("When the name is not a project title and the PROJECTS NAMED IN THIS QUESTION block gives no title "
            "for it (it is only an investigation or a study, or it is in none of the lists), match it "
            "case-insensitively") in bullet


def test_the_block_names_the_level():
    block = gctx.render_project_titles({"Alpha Cohort": "Gamma Lab"}, {"Alpha Cohort": "investigation"})
    assert '- "Alpha Cohort" is the investigation titled "Gamma Lab"' in block


def _inv_scope(project_titles, title):
    return describe_query_scope(
        entity_result=EntityAgentOutput(projects=["Alpha Cohort"]).model_dump(),
        parser_plan=ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE p.title = $t RETURN count(s) AS n",
                    "parameters": {"t": title}, "project_titles": project_titles},
        user_query="How many samples are in the Alpha Cohort?")


def test_the_scope_check_keeps_the_narrower_name_not_applied_under_a_broader_title():
    """A broader hit never enters project_titles, so the query on the owner's title leaves the name NOT APPLIED."""
    assert "project Alpha Cohort" in _inv_scope({}, "Gamma Group").not_applied


def test_the_scope_check_applies_the_name_when_its_own_title_is_in_the_query():
    assert "project Alpha Cohort" in _inv_scope({"Alpha Cohort": "Gamma Lab"}, "Gamma Lab").applied


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


def test_without_the_title_map_an_exact_title_scope_is_applied_from_the_query():
    """r7-708, SPEC-F route 3 (container title): the query compared p.title to 'IMPAcTb', so Impact is applied."""
    scope = _scope(None)

    assert not scope.not_applied, scope.not_applied
    assert "project Impact" in scope.applied and 'keyword "Impact"' in scope.applied


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
