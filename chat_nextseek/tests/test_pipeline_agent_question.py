"""Question sub-mode: catalog + capabilities Q&A, then clear state."""
from unittest.mock import MagicMock, patch

from chat_nextseek import pipeline_agent
from chat_nextseek.schemas.pipeline import DirectiveParseOutput


def test_question_path_answers_and_clears():
    session = {}
    parsed = DirectiveParseOutput(sub_mode="question")
    with patch("chat_nextseek.pipeline_agent._pipeline_directive_parse", return_value=parsed), \
         patch("chat_nextseek.pipeline_agent._pipeline_question_step",
               return_value="rnaseq, scrnaseq, atacseq, ... [enumerated]"):
        out = pipeline_agent.start(session, MagicMock(),
                                   user_query="what pipelines are available?",
                                   parser_plan={}, reporter_plan={})
    assert out["action"] == "passthrough"
    assert "rnaseq" in out["reply"]
    assert session.get("pipeline_agent") in (None, {})


def test_question_path_uses_pinned_bundle_for_targeted_query():
    """When the user references 'these mice', the question step gets a
    bundle summary so it can answer specifically."""
    session = {
        "results_history": [{"user_query": "find NHPs",
                             "api_result_full": {"data": {"rows": [{"uid": "NHP-1"}]}}}],
    }
    parsed = DirectiveParseOutput(sub_mode="question")
    captured = {}

    def fake_q(*, config, user_query, pinned_bundle_summary):
        captured["pinned"] = pinned_bundle_summary
        return "No GSEA outputs found in the lineage."

    with patch("chat_nextseek.pipeline_agent._pipeline_directive_parse", return_value=parsed), \
         patch("chat_nextseek.pipeline_agent._pipeline_question_step", side_effect=fake_q):
        pipeline_agent.start(session, MagicMock(),
                             user_query="have we run GSEA on these mice?",
                             parser_plan={}, reporter_plan={})
    assert "NHPs" in captured["pinned"]
