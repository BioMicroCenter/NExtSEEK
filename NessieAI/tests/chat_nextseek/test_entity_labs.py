"""The entity agent resolves lab and person names in code after the LLM returns.

Spec `docs/superpowers/specs/2026-09-18-projects-labs-context.md` sections 7.5 and 7.6.
The matcher's rules are pinned in `test_lab_code.py`; this file pins the wiring: that
`entity_agent` reads `config.LABS`, the catalogs and the projects catalog, overwrites
`labs`, `lab_codes` and `lab_matches`, and merges `scientists` and `keywords`, on every
path the LLM call can take. Every lab here is invented.
"""
from unittest.mock import MagicMock, patch

import pytest

import chat_nextseek.helpers.lab_code as lab_code_module
from chat_nextseek.agents.entity import entity_agent
from chat_nextseek.schemas.entity import EntityAgentOutput, LabMatch


def _rec(code, name, affiliation, institution_id, project_ids):
    return {
        "code": code, "name": name, "affiliation": affiliation,
        "title": f"{code}-{name} Lab ({affiliation})",
        "institution_id": institution_id, "project_ids": project_ids,
    }


LABS = [
    _rec("ASH", "Ashgrove", "BWH", 41, [4, 12]),
    _rec("FEN", "Fenwick", "MIT", 43, [4]),
    _rec("FEW", "Fenwick", "Harvard", 44, []),
    _rec("MAR", "Marrow", "BWH", 42, [7]),
]
SAMPLETYPES = [
    {"SampleType": "BMA", "ID": 1, "Name": "Bone Marrow Aspirate",
     "Description": "Cells aspirated from the bone marrow.", "Tags": "marrow, aspirate"},
]
ASSAYS = [{"Name": "Flow Cytometry", "Description": "Counts stained cells.", "Tags": "FACS"}]
PROJECTS = [
    {"name": "Tidewater", "entity_type": "project", "project_id": 4,
     "alternative_names": ["Hollowell Study"]},
]


def _config(labs=LABS):
    config = MagicMock()
    config.get_agent_model.return_value = (MagicMock(), "model", None)
    if labs is not None:
        config.LABS = labs
    config.MIN_SAMPLETYPES = SAMPLETYPES
    config.MIN_ASSAYS = ASSAYS
    config.MIN_PROJECTS = PROJECTS
    return config


def _run(question, llm_out, config=None, **kw):
    config = config if config is not None else _config()
    with patch("chat_nextseek.agents.entity.call_llm_structured", return_value=llm_out):
        return entity_agent(config, user_query=question, **kw)


# --------------------------------------------------------------------------
# 7.5, verbatim
# --------------------------------------------------------------------------

def test_the_worked_example_of_75():
    out = _run("RNA from the Fenwick lab, handled by Dana Example",
               EntityAgentOutput(labs=["Fenwick", "Dana Example"]))

    assert out.labs == ["Fenwick"]
    assert out.lab_codes == ["FEN", "FEW"]
    assert out.lab_matches == [
        LabMatch(text="Fenwick lab", code="FEN", name="Fenwick", affiliation="MIT",
                 project_ids=[4], rule="lab_phrase", ambiguous=True),
        LabMatch(text="Fenwick lab", code="FEW", name="Fenwick", affiliation="Harvard",
                 project_ids=[], rule="lab_phrase", ambiguous=True),
    ]
    assert out.scientists == ["Dana Example"]
    assert out.keywords == ["Dana Example"]


# --------------------------------------------------------------------------
# 7.6, every row, through the agent
# --------------------------------------------------------------------------

CASES_76 = [
    # question, LLM labs, codes, rules, scientists, keywords
    ("RNA from the Ashgrove lab", ["Ashgrove"], ["ASH"], ["lab_phrase"], [], []),
    ("samples from Jane Ashgrove", ["Jane Ashgrove"], ["ASH"], ["name"], [], []),
    ("Ashgrove, J. samples", ["Ashgrove, J."], ["ASH"], ["name"], [], []),
    ("Ashgrove's mice", [], ["ASH"], ["possessive"], [], []),
    ("ASH lab samples", ["ASH"], ["ASH"], ["code"], [], []),
    ("bone marrow samples", [], [], [], [], []),
    ("samples from the Marrow lab", ["Marrow"], ["MAR"], ["lab_phrase"], [], []),
    ("marrow samples", ["marrow"], [], [], [], ["marrow"]),
    ("the Fenwick lab at Harvard", ["Fenwick"], ["FEW"], ["lab_phrase"], [], []),
    ("samples handled by Dana Example", ["Dana Example"], [], [], ["Dana Example"], ["Dana Example"]),
    ("the Oakley lab", ["Oakley"], [], [], ["Oakley"], ["Oakley"]),
    ("XYZ lab", ["XYZ"], [], [], [], ["XYZ"]),
]


@pytest.mark.parametrize("question,llm_labs,codes,rules,scientists,keywords", CASES_76)
def test_every_worked_case_of_76(question, llm_labs, codes, rules, scientists, keywords):
    out = _run(question, EntityAgentOutput(labs=llm_labs))

    assert out.lab_codes == codes
    assert [m.rule for m in out.lab_matches] == rules
    assert out.scientists == scientists
    assert out.keywords == keywords


# --------------------------------------------------------------------------
# What the agent reads and overwrites
# --------------------------------------------------------------------------

def test_codes_and_matches_the_llm_wrote_are_overwritten():
    llm = EntityAgentOutput(
        labs=["Kestrel"], lab_codes=["KES"],
        lab_matches=[LabMatch(text="x", code="KES", name="Kestrel", rule="name")],
    )
    out = _run("RNA from the Ashgrove lab", llm)

    assert out.lab_codes == ["ASH"]
    assert [m.code for m in out.lab_matches] == ["ASH"]


def test_the_llms_scientists_and_keywords_are_kept():
    llm = EntityAgentOutput(labs=[], scientists=["Pat Sample"], keywords=["RNA"])
    out = _run("RNA handled by Pat Sample", llm)

    assert out.scientists == ["Pat Sample"]
    assert out.keywords == ["RNA", "Pat Sample"]
    assert out.lab_codes == []


def test_the_catalog_rule_reads_the_config_catalogs():
    """M5 comes from config.MIN_SAMPLETYPES / MIN_ASSAYS, not a hand-kept list."""
    with_catalog = _run("marrow samples", EntityAgentOutput(labs=["marrow"]))
    config = _config()
    config.MIN_SAMPLETYPES = []
    without_catalog = _run("marrow samples", EntityAgentOutput(labs=["marrow"]), config=config)

    assert with_catalog.lab_codes == []
    assert without_catalog.lab_codes == ["MAR"]


def test_a_project_alias_in_labs_is_dropped_using_the_projects_catalog():
    out = _run("the Hollowell Study samples", EntityAgentOutput(labs=["Hollowell Study"]))

    assert out.labs == []
    assert out.keywords == []
    assert out.scientists == []


def test_the_projects_catalog_passed_in_is_the_one_read():
    out = _run("the Hollowell Study samples", EntityAgentOutput(labs=["Hollowell Study"]),
               sampletypes=[], assays=[], projects=[])

    assert out.scientists == ["Hollowell Study"]


def test_resolution_also_runs_after_the_raw_fallback():
    config = _config()
    client = MagicMock()
    client.chat.return_value = MagicMock(content='{"labs": ["Jane Ashgrove"]}')
    config.get_agent_model.return_value = (client, "model", None)
    with patch("chat_nextseek.agents.entity.call_llm_structured", side_effect=ValueError("bad json")), \
            patch("chat_nextseek.agents.entity.log_usage"):
        out = entity_agent(config, user_query="RNA from Jane Ashgrove")

    # A first-three-letters rule would say JAN; the record says ASH.
    assert out.lab_codes == ["ASH"]
    assert out.labs == ["Ashgrove"]
    assert [m.rule for m in out.lab_matches] == ["name"]


# --------------------------------------------------------------------------
# No labs document
# --------------------------------------------------------------------------

def test_unavailable_labs_pass_through_with_one_warning(capsys):
    """A MagicMock config has no real LABS list: that means unavailable."""
    config = _config(labs=None)
    out = _run("RNA from the Ashgrove lab, handled by Dana Example",
               EntityAgentOutput(labs=["Ashgrove", "Dana Example"], lab_codes=["ASH"]),
               config=config)

    assert out.labs == ["Ashgrove", "Dana Example"]
    assert out.lab_codes == []
    assert out.lab_matches == []
    assert out.scientists == []
    printed = capsys.readouterr().out
    assert printed.count("[WARN][ENTITY]") == 1


def test_a_matcher_failure_degrades_to_unresolved_labs_never_a_guessed_code(capsys):
    """The entity agent degrades gracefully; a lab resolution bug must not fail the turn."""
    with patch("chat_nextseek.agents.entity.resolve_labs", side_effect=RuntimeError("boom")):
        out = _run("RNA from the Ashgrove lab",
                   EntityAgentOutput(labs=["Ashgrove"], lab_codes=["ASH"], keywords=["RNA"]))

    assert out.labs == ["Ashgrove"]
    assert out.lab_codes == []
    assert out.lab_matches == []
    assert out.keywords == ["RNA"]
    assert "[WARN][ENTITY]" in capsys.readouterr().out


def test_the_first_three_letters_rule_is_gone():
    """E5: lab_code() derived a code from any name; its only caller now uses the matcher."""
    assert not hasattr(lab_code_module, "lab_code")


# --------------------------------------------------------------------------
# The two new fields (spec 7.5). The CC hook injects model_dump(), so both must
# survive it, and an LLM reply that never mentions them must still validate.
# --------------------------------------------------------------------------

def test_new_fields_default_to_empty_lists():
    out = EntityAgentOutput()
    dumped = out.model_dump()
    assert dumped["scientists"] == []
    assert dumped["lab_matches"] == []


def test_lab_match_defaults():
    match = LabMatch(text="Ashgrove lab", code="ASH", name="Ashgrove", rule="lab_phrase")
    assert match.affiliation is None
    assert match.project_ids == []
    assert match.ambiguous is False


def test_new_fields_survive_model_dump_and_validate_back():
    out = EntityAgentOutput(
        labs=["Fenwick"],
        lab_codes=["FEN", "FEW"],
        scientists=["Dana Example"],
        keywords=["Dana Example"],
        lab_matches=[
            LabMatch(text="Fenwick lab", code="FEN", name="Fenwick", affiliation="MIT",
                     project_ids=[4], rule="lab_phrase", ambiguous=True),
            LabMatch(text="Fenwick lab", code="FEW", name="Fenwick", affiliation="Harvard",
                     project_ids=[], rule="lab_phrase", ambiguous=True),
        ],
    )
    dumped = out.model_dump()
    assert dumped["scientists"] == ["Dana Example"]
    assert dumped["lab_matches"][0] == {
        "text": "Fenwick lab", "code": "FEN", "name": "Fenwick", "affiliation": "MIT",
        "project_ids": [4], "rule": "lab_phrase", "ambiguous": True,
    }
    assert EntityAgentOutput.model_validate(dumped) == out


def test_an_llm_reply_without_the_new_fields_still_validates():
    out = EntityAgentOutput.model_validate({"labs": ["Ashgrove"], "unexpected": 1})
    assert out.scientists == []
    assert out.lab_matches == []
