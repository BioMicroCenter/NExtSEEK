"""A cancer the question names, as the TCGA study it is stored under (``chat_nextseek.tcga_studies``).

The failure this guards: "Our esophageal tumors all show CCND1 amplification. What TCGA patients could I analyze to
see if they also have this amplification?" was answered with 190 patients found by matching "esophageal" as free
text, bladder, biliary and testis patients among them, and the reply said CNV records have no gene-level field. The
answer is TCGA's Study 'ESCA' (185 patients with copy-number files, squamous 96 and adenocarcinoma 89 by primary
diagnosis), each with gene-level copy-number files that hold CCND1. Every fixture here is synthetic.
"""
from __future__ import annotations

import pathlib

import pytest

import chat_nextseek
from chat_nextseek import graph_context as gc
from chat_nextseek import tcga_studies as tcga
from chat_nextseek.helpers.query_scope import describe_query_scope
from chat_nextseek.schemas.entity import EntityAgentOutput, EntityItem
from chat_nextseek.schemas.router import ParserPlan

QUESTION = ("Our esophageal tumors all show CCND1 amplification. What TCGA patients could I analyze to see if they "
            "also have this amplification?")
PROMPTS = pathlib.Path(chat_nextseek.__file__).resolve().parent / "prompts"
TCGA_TITLES = [study.code for study in tcga.TCGA_STUDIES]


# --------------------------------------------------------------------------------------------------------------------
# The map
# --------------------------------------------------------------------------------------------------------------------

def test_the_map_is_gdcs_33_tcga_projects():
    assert len(TCGA_TITLES) == 33 == len(set(TCGA_TITLES))
    assert tcga.BY_CODE["ESCA"].name == "esophageal carcinoma"


@pytest.mark.parametrize("text, codes", [
    (QUESTION, ["ESCA"]),
    ("oesophageal adenocarcinoma in TCGA", ["ESCA"]),
    ("Which TCGA lung patients have RNA-seq?", ["LUAD", "LUSC"]),
    ("TCGA renal and kidney tumours", ["KICH", "KIRC", "KIRP"]),
    ("TCGA colorectal", ["COAD", "READ"]),
    ("TCGA gastric cancer", ["STAD"]),
    ("TCGA uveal melanoma", ["UVM"]),  # not skin melanoma too
    ("TCGA melanoma", ["SKCM"]),
    ("TCGA hepatocellular carcinoma", ["LIHC"]),
])
def test_a_cancer_word_names_its_studies(text, codes):
    assert [hit.code for hit in tcga.studies_named(text)] == codes


@pytest.mark.parametrize("text", [
    "TCGA gallbladder samples",  # not "bladder"
    "TCGA adrenal medulla",  # "renal" sits inside "adrenal"
    "How many TCGA patients are there?",
])
def test_a_word_inside_another_word_names_no_study(text):
    assert not any(hit.code in {"BLCA", "KICH", "KIRC", "KIRP"} for hit in tcga.studies_named(text))


def test_the_hit_carries_the_word_as_written():
    assert tcga.studies_named(QUESTION)[0].word == "esophageal"


def test_only_the_studies_this_box_holds_are_named():
    assert tcga.studies_named(QUESTION, codes=["LIHC", "BRCA"]) == []


@pytest.mark.parametrize("word, code, names", [
    ("esophageal", "ESCA", True), ("Esophageal carcinoma", "ESCA", True), ("esophageal", "esca", True),
    ("esophageal tumors", "ESCA", True), ("lung", "LUSC", True), ("lung adenocarcinoma", "LUAD", True),
    # A narrower word than the study is not applied by the study scope.
    ("lung adenocarcinoma", "LUSC", False), ("esophageal squamous", "ESCA", False),
    ("bladder", "ESCA", False), ("esophageal", "NOT-A-CODE", False), ("", "ESCA", False),
])
def test_word_names_study(word, code, names):
    assert tcga.word_names_study(word, code) is names


# --------------------------------------------------------------------------------------------------------------------
# The graph agent's block
# --------------------------------------------------------------------------------------------------------------------

def test_the_block_tells_the_graph_agent_which_title_esophageal_means():
    block = gc.tcga_studies_block(TCGA_TITLES, QUESTION)
    assert block.render() == (gc.TCGA_STUDIES_HEADING + ':\n- "esophageal" is the study titled "ESCA" '
                              "(esophageal carcinoma)")


def test_no_block_without_tcga_in_the_question():
    # A mouse study's "liver tumours" is never steered to TCGA's liver cohort.
    assert gc.tcga_studies_block(TCGA_TITLES, "Which liver tumours do the NDMA mice have?") is None


def test_no_block_on_a_box_without_tcga():
    assert gc.tcga_studies_block(["MetNet", "GBM_BTC Unpublished"], QUESTION) is None


def test_render_vocabulary_leads_with_the_block_without_a_study_word():
    vocab = {"investigation_titles": ["TCGA", "Jones"], "project_titles": ["TCGA"], "study_titles": TCGA_TITLES}
    text = gc.render_vocabulary(vocab, QUESTION)
    assert text.startswith(gc.TCGA_STUDIES_HEADING)
    assert '"esophageal" is the study titled "ESCA"' in text
    # The question has no study word, so the full study list stays gated off, as before.
    assert "STUDY TITLES (Study.title)" not in text


def test_the_block_survives_a_budget_that_trims_the_titles():
    vocab = {"investigation_titles": [f"Investigation {i}" for i in range(400)], "study_titles": TCGA_TITLES}
    text = gc.render_vocabulary(vocab, QUESTION, budget=1024)
    assert '"esophageal" is the study titled "ESCA"' in text


# --------------------------------------------------------------------------------------------------------------------
# The reply is told the cancer was applied when the query scoped its study
# --------------------------------------------------------------------------------------------------------------------

def _scope(study_title):
    return describe_query_scope(
        entity_result=EntityAgentOutput(keywords=["esophageal"],
                                        sampletypes=[EntityItem(code="PAT", name="Patient")]).model_dump(),
        parser_plan=ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (p:T_PAT)-[:IN_STUDY]->(st:Study) WHERE st.title = $study "
                              "RETURN count(DISTINCT p) AS n", "parameters": {"study": study_title}},
        user_query=QUESTION,
    )


def test_a_cancer_word_over_its_tcga_study_is_applied():
    assert not any("esophageal" in item for item in _scope("ESCA").not_applied)


def test_a_cancer_word_over_another_study_stays_a_gap():
    assert any("esophageal" in item for item in _scope("LIHC").not_applied)


# --------------------------------------------------------------------------------------------------------------------
# The prompts
# --------------------------------------------------------------------------------------------------------------------

def _graph_prompt():
    return (PROMPTS / "graph_agent.txt").read_text(encoding="utf-8")


def _section(text, start, end):
    return text[text.index(start):text.index(end)]


def test_the_graph_prompt_scopes_a_named_cancer_on_its_study_not_its_word():
    step5 = _section(_graph_prompt(), "## STEP 5:", "## STEP 6:")
    rule = step5[step5.index("- **A cancer the TCGA STUDIES NAMED IN THIS QUESTION block maps"):]
    rule = rule[:rule.index("\n- ")]
    assert "st.title = $study" in rule and "never on the cancer's word in `search_text`" in rule
    assert "st.title IN $studies" in rule


def test_the_graph_prompt_splits_a_tcga_cohort_by_primary_diagnosis():
    step5 = _section(_graph_prompt(), "## STEP 5:", "## STEP 6:")
    assert "`DiagnosisIsPrimaryDisease` 'true', field `PrimaryDiagnosis`" in step5
    assert "group and filter on `PrimaryDiagnosis`" in step5


def test_the_graph_prompt_keeps_the_askers_cohort_apart_from_tcga():
    step5 = _section(_graph_prompt(), "## STEP 5:", "## STEP 6:")
    assert "told apart by a `cohort` column, never merged" in step5
    assert "THEN 'TCGA' ELSE 'yours' END AS cohort" in step5


def test_the_graph_prompt_finds_a_gene_through_its_gene_level_files():
    step6 = _section(_graph_prompt(), "## STEP 6:", "## When a query matched nothing")
    rule = step6[step6.index("- **A GENE'S COPY NUMBER"):]
    rule = rule[:rule.index("\n- ")]
    assert "'Gene Level Copy Number'" in rule
    assert "Declare the gene and the change words in `keyword_fields` against `Type`" in rule


def test_the_chatter_never_calls_gene_data_in_files_a_missing_field():
    chatter = (PROMPTS / "chatter_agent.txt").read_text(encoding="utf-8")
    graph_mode = _section(chatter, "MODE: graph_query", "MODE: memory_computed")
    assert "Never write that the records have no gene-level field" in graph_mode
    assert "report each cohort on its own, never as one total" in graph_mode
