from unittest.mock import MagicMock, patch

from chat_nextseek.agents import reporter as reporter_mod
from chat_nextseek.schemas import ReportCoderOutput


def test_report_coder_output_defaults():
    out = ReportCoderOutput(extraction_code="result = {}")
    assert out.extraction_code == "result = {}"
    assert out.result_description == ""
    assert out.fields_used == []
    assert out.notes == ""


def test_report_coder_agent_strips_code_fences():
    cfg = MagicMock()
    cfg.REPORT_CODER_SYSTEM_PROMPT = "sys"
    cfg.LOG_DIR = "/tmp"
    cfg.get_agent_model.return_value = (MagicMock(), "model", 16000)

    fenced = ReportCoderOutput(extraction_code="```python\nresult = {'samples': []}\n```")
    with patch.object(reporter_mod, "call_llm_structured", return_value=fenced), \
         patch.object(reporter_mod, "log_prompt", lambda *a, **k: None):
        out = reporter_mod.report_coder_agent(
            cfg, user_query="make a GEO report", report_type="GEO",
            template={"samples": [{}]}, metadata={"data": {"data": []}},
        )
    assert out.extraction_code == "result = {'samples': []}"
