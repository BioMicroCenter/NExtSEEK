"""Pipeline-agent state lifecycle: is_active, clear, snapshot_for_chat_log."""
from chat_nextseek import pipeline_agent


def test_is_active_false_on_empty_session():
    session = {}
    assert pipeline_agent.is_active(session) is False


def test_is_active_false_on_done_phase():
    session = {"pipeline_agent": {"active": False, "phase": "done"}}
    assert pipeline_agent.is_active(session) is False


def test_is_active_true_on_awaiting_validation():
    session = {"pipeline_agent": {"active": True, "phase": "awaiting_validation"}}
    assert pipeline_agent.is_active(session) is True


def test_is_active_true_on_awaiting_groupby_clarification():
    session = {"pipeline_agent": {
        "active": True, "phase": "awaiting_groupby_clarification"
    }}
    assert pipeline_agent.is_active(session) is True


def test_clear_wipes_state():
    session = {"pipeline_agent": {"active": True, "phase": "awaiting_validation"}}
    pipeline_agent.clear(session)
    assert pipeline_agent.is_active(session) is False
    assert session["pipeline_agent"] == {}


def test_snapshot_includes_phase_pipeline_and_counts():
    session = {"pipeline_agent": {
        "active": True,
        "phase": "awaiting_validation",
        "directive": {"pipeline_key": "rnaseq", "group_by_phrase": "exposure"},
        "resolution": {
            "source_uids": ["NHP-1", "NHP-2"],
            "leaves_filtered": [{"uid": "D.SEQ-1"}, {"uid": "D.SEQ-2"}],
        },
        "samplesheet_rows": [{}, {}, {}],
    }}
    snap = pipeline_agent.snapshot_for_chat_log(session)
    assert snap["phase"] == "awaiting_validation"
    assert snap["pipeline"] == "rnaseq"
    assert snap["group_by_phrase"] == "exposure"
    assert snap["source_uid_count"] == 2
    assert snap["leaf_count"] == 2
    assert snap["samplesheet_row_count"] == 3


def test_snapshot_empty_state_returns_empty_dict():
    assert pipeline_agent.snapshot_for_chat_log({}) == {}
