"""Verify the orchestrator routes NFCORE intents to pipeline_agent, not
nfcore_wizard."""
from unittest.mock import MagicMock, patch

from chat_nextseek import orchestrator


def test_active_pipeline_agent_intercepts_turn():
    """When pipeline_agent.is_active() returns True, _handle_wizard_turn (or
    its pipeline_agent equivalent gate) must short-circuit and call
    pipeline_agent.handle_turn — without ever consulting nfcore_wizard."""
    session = {"pipeline_agent": {"active": True, "phase": "awaiting_validation"}}
    config = MagicMock()
    send_event = MagicMock()
    artifact_store = MagicMock()

    with patch("chat_nextseek.orchestrator.pipeline_agent.is_active",
               return_value=True), \
         patch("chat_nextseek.orchestrator.pipeline_agent.handle_turn",
               return_value={"action": "ask", "reply": "ok", "params": None}) as ht, \
         patch("chat_nextseek.orchestrator.pipeline_agent.snapshot_for_chat_log",
               return_value={"phase": "awaiting_validation"}), \
         patch("chat_nextseek.orchestrator.nfcore_wizard.is_active",
               return_value=False) as wiz_is_active, \
         patch("chat_nextseek.orchestrator.nfcore_wizard.handle_turn") as wiz_handle, \
         patch("chat_nextseek.orchestrator.nfcore_wizard.start") as wiz_start:
        result = orchestrator._handle_pipeline_agent_turn(
            session=session,
            config=config,
            user_text="anything",
            log_dir="/tmp/x",
            send_event=send_event,
            artifact_store=artifact_store,
        )

    ht.assert_called_once()
    wiz_handle.assert_not_called()
    wiz_start.assert_not_called()
    assert result is not None
    assert result.get("reply") == "ok"


def test_pipeline_agent_intercepts_before_wizard_in_run_query():
    """End-to-end: pipeline_agent must gate BEFORE the wizard interceptor.
    When both are 'active', pipeline_agent.handle_turn runs and the wizard
    never sees the turn."""
    session = {
        "pipeline_agent": {"active": True, "phase": "awaiting_validation"},
        # Wizard also reports active — pipeline_agent must still win.
        "nfcore_wizard": {"active": True, "step": "builder"},
    }
    config = MagicMock()
    config.API_USER = "u"
    config.API_PASS = "p"

    with patch("chat_nextseek.orchestrator.pipeline_agent.is_active",
               return_value=True), \
         patch("chat_nextseek.orchestrator.pipeline_agent.handle_turn",
               return_value={"action": "ask", "reply": "pa-reply", "params": None}) as ht, \
         patch("chat_nextseek.orchestrator.pipeline_agent.snapshot_for_chat_log",
               return_value={"phase": "awaiting_validation"}), \
         patch("chat_nextseek.orchestrator.nfcore_wizard.is_active",
               return_value=True) as wiz_is_active, \
         patch("chat_nextseek.orchestrator.nfcore_wizard.handle_turn") as wiz_handle, \
         patch("chat_nextseek.orchestrator.nfcore_wizard.start") as wiz_start, \
         patch("chat_nextseek.orchestrator._ensure_query_log_dir", return_value="/tmp/log"), \
         patch("chat_nextseek.orchestrator.ArtifactStore"), \
         patch("chat_nextseek.orchestrator.entity_agent"), \
         patch("chat_nextseek.orchestrator.parser_agent"), \
         patch("chat_nextseek.orchestrator.reporter_agent"), \
         patch("chat_nextseek.orchestrator.shortlist_catalog",
               return_value=([], [])):
        result = orchestrator.run_query(
            session=session,
            config=config,
            user_text="anything",
        )

    ht.assert_called_once()
    # Wizard interceptor must never have advanced.
    wiz_handle.assert_not_called()
    wiz_start.assert_not_called()
    assert result.get("reply") == "pa-reply"


def test_nfcore_activation_calls_pipeline_agent_start_not_wizard_start():
    """When a fresh NFCORE intent arrives (reporter_mode=report_generation,
    report_type starts with NFCORE), the orchestrator must call
    pipeline_agent.start — never nfcore_wizard.start."""
    from chat_nextseek.schemas import ParserPlan, ParserFilters
    from chat_nextseek.schemas.chat import ReporterPlan
    from chat_nextseek.schemas import EntityAgentOutput

    session = {
        "results_history": [],
        "last_files": [],
    }
    config = MagicMock()
    config.API_USER = "u"
    config.API_PASS = "p"

    entity_out = EntityAgentOutput()

    parser_plan = ParserPlan(
        mode="reporter",
        report_mode="report_generation",
        report_type="NFCORE_RNASEQ",
        intent_summary="run rnaseq",
        filters=ParserFilters(uids=["NHP-1"]),
    )
    reporter_plan = ReporterPlan(
        reporter_mode="report_generation",
        report_type="NFCORE_RNASEQ",
        uids=["NHP-1"],
        reporter_context={"per_sample_reports": False},
        notes="",
    )

    pa_start_payload = {"action": "ask", "reply": "pa-start-reply", "params": None}

    with patch("chat_nextseek.orchestrator.pipeline_agent.is_active",
               return_value=False), \
         patch("chat_nextseek.orchestrator.pipeline_agent.start",
               return_value=pa_start_payload) as pa_start, \
         patch("chat_nextseek.orchestrator.pipeline_agent.snapshot_for_chat_log",
               return_value={"phase": "awaiting_validation"}), \
         patch("chat_nextseek.orchestrator.nfcore_wizard.is_active",
               return_value=False), \
         patch("chat_nextseek.orchestrator.nfcore_wizard.start") as wiz_start, \
         patch("chat_nextseek.orchestrator._ensure_query_log_dir", return_value="/tmp/log"), \
         patch("chat_nextseek.orchestrator.ArtifactStore"), \
         patch("chat_nextseek.orchestrator.entity_agent", return_value=entity_out), \
         patch("chat_nextseek.orchestrator.parser_agent", return_value=parser_plan), \
         patch("chat_nextseek.orchestrator.reporter_agent", return_value=reporter_plan), \
         patch("chat_nextseek.orchestrator.fix_sample_endpoint",
               side_effect=lambda d: d), \
         patch("chat_nextseek.orchestrator.shortlist_catalog",
               return_value=([], [])):
        result = orchestrator.run_query(
            session=session,
            config=config,
            user_text="run rnaseq on NHP-1",
        )

    pa_start.assert_called_once()
    wiz_start.assert_not_called()
    assert result.get("reply") == "pa-start-reply"
