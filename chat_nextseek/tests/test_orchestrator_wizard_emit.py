"""Tests for the empty-uids guard in _execute_nfcore_wizard."""
from unittest.mock import MagicMock, patch

from chat_nextseek.orchestrator import _execute_nfcore_wizard


def test_execute_nfcore_wizard_refuses_with_empty_uids_and_bounces_to_builder():
    """If uids is empty, generate_report_outputs must NOT run; the wizard
    bounces back to STEP_BUILDER and the user gets a friendly message."""
    session = {
        "nfcore_wizard": {
            "active": True, "step": "confirm",
            "pipeline": "rnaseq",
            "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []},
        },
        "results_history": [],
    }
    config = MagicMock()
    send_event = MagicMock()
    artifact_store = MagicMock()
    wizard_params = {
        "uids": [],
        "report_type": "NFCORE_RNASEQ",
        "pre_supplied_cohorts": [],
        "selector_rationale": "test",
    }

    with patch("chat_nextseek.orchestrator.generate_report_outputs") as gen:
        result = _execute_nfcore_wizard(
            session=session,
            config=config,
            user_text="yes",
            wizard_params=wizard_params,
            log_dir="/tmp/x",
            send_event=send_event,
            artifact_store=artifact_store,
            confirmation_reply="ok",
        )

    # generate_report_outputs must NOT have been called.
    gen.assert_not_called()
    # Wizard bounced to builder step.
    assert session["nfcore_wizard"]["step"] == "builder"
    # Returned result is a dict with a reply that explains the situation.
    assert isinstance(result, dict)
    reply = result.get("reply", "")
    assert "sample" in reply.lower() or "uid" in reply.lower() or "pick" in reply.lower()
