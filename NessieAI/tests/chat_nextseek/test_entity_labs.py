from unittest.mock import MagicMock, patch

from chat_nextseek.agents.entity import entity_agent
from chat_nextseek.schemas.entity import EntityAgentOutput, LabMatch


def test_entity_derives_lab_codes_from_labs():
    """The LLM detects lab surnames; lab_codes are derived deterministically."""
    config = MagicMock()
    config.get_agent_model.return_value = (MagicMock(), "model", None)
    llm_out = EntityAgentOutput(labs=["Kamm", "Shalek lab"])
    with patch("chat_nextseek.agents.entity.call_llm_structured", return_value=llm_out):
        out = entity_agent(
            config,
            user_query="organ on chips in the Kamm lab",
            sampletypes=[],
            assays=[],
            projects=[],
        )
    assert out.lab_codes == ["KAM", "SHA"]


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
