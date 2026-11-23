"""A bare count or list of the SOPs on file runs the protocols report, whatever summary the reporter model picks.

The parser's SOP step sends the turn to the reporter with ``SOP_RECORDS_NOTE``; ``summary_mode`` was the reporter
model's pick, and its default when unclear is ``samples``, which answers "how many SOPs" with a sample count.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from chat_nextseek import orchestrator
from chat_nextseek.agents.parser import SOP_RECORDS_NOTE
from chat_nextseek.schemas import EntityAgentOutput, ParserPlan
from chat_nextseek.schemas.chat import ReporterPlan


class _Stop(Exception):
    pass


def _summary_plan_for(notes: str, summary_mode: str | None) -> ReporterPlan:
    plan = ParserPlan(mode="reporter", report_mode="summary", intent_summary="SOPs on file", notes=notes)
    seen = []

    def summary(config, reporter_plan, log_dir, **kwargs):
        seen.append(reporter_plan)
        raise _Stop

    config = MagicMock()
    config.API_USER, config.API_PASS = "u", "p"
    with patch("chat_nextseek.orchestrator.pipeline_agent.is_active", return_value=False), \
         patch("chat_nextseek.orchestrator._ensure_query_log_dir", return_value="/tmp/log"), \
         patch("chat_nextseek.orchestrator.ArtifactStore"), \
         patch("chat_nextseek.orchestrator.entity_agent", return_value=EntityAgentOutput()), \
         patch("chat_nextseek.orchestrator.parser_agent", return_value=plan), \
         patch("chat_nextseek.orchestrator.reporter_agent",
               return_value=ReporterPlan(reporter_mode=None, summary_mode=summary_mode)), \
         patch("chat_nextseek.orchestrator.run_reporter_summary", side_effect=summary), \
         patch("chat_nextseek.orchestrator.fix_sample_endpoint", side_effect=lambda d: d), \
         patch("chat_nextseek.orchestrator.shortlist_catalog",
               return_value=([], [], {"sampletype_codes": [], "assay_codes": [], "sampletype_ranks": {},
                                      "assay_ranks": {}, "enabled": False, "fallback_reason": None})):
        try:
            orchestrator.run_query(session={"results_history": [], "last_files": []}, config=config,
                                   user_text="How many SOPs are on file?")
        except _Stop:
            pass
    assert len(seen) == 1, "the summary report never ran"
    return seen[0]


def test_the_sop_records_note_runs_the_protocols_report():
    assert _summary_plan_for(SOP_RECORDS_NOTE, None).summary_mode == "protocols"
    assert _summary_plan_for("an earlier note | " + SOP_RECORDS_NOTE, "samples").summary_mode == "protocols"


def test_a_plan_without_the_note_keeps_the_models_pick():
    assert _summary_plan_for("", "published").summary_mode == "published"
    assert _summary_plan_for("", None).summary_mode is None
