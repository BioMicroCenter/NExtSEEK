from unittest.mock import MagicMock, patch

from chat_nextseek.agents.entity import entity_agent
from chat_nextseek.schemas.entity import EntityAgentOutput


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
