from unittest.mock import MagicMock, patch

from chat_nextseek.pipeline.builder_tools import (
    BUILDER_TOOL_SCHEMAS,
    dispatch_tool_call,
)


def test_builder_tool_schemas_has_required_tools():
    names = {t["name"] for t in BUILDER_TOOL_SCHEMAS}
    assert names == {
        "memory_query",
        "refine_last_search",
        "advanced_search",
        "passthrough_to_parser",
        "finalize_turn",
    }


def test_builder_tool_schemas_are_well_formed():
    """Each tool dict must have name, description, input_schema."""
    for tool in BUILDER_TOOL_SCHEMAS:
        assert "name" in tool and isinstance(tool["name"], str)
        assert "description" in tool and isinstance(tool["description"], str)
        assert "input_schema" in tool and tool["input_schema"]["type"] == "object"


def test_dispatch_memory_query_calls_memory_agent():
    session = {
        "results_history": [{"raw_result_path": "/tmp/b.json", "bundle_id": 7}],
        "nfcore_wizard": {"pinned_context": {"bundle_id": 7}},
    }
    config = MagicMock()
    with patch("chat_nextseek.pipeline.builder_tools.memory_agent_answer") as m:
        m.return_value = "There are 195 mice."
        out = dispatch_tool_call(
            config=config,
            session=session,
            tool_name="memory_query",
            tool_input={"question": "how many mice?"},
        )
    assert out == "There are 195 mice."
    m.assert_called_once()


def test_dispatch_memory_query_without_pinned_bundle_returns_helpful_text():
    session = {"results_history": [], "nfcore_wizard": {}}
    config = MagicMock()
    out = dispatch_tool_call(
        config=config,
        session=session,
        tool_name="memory_query",
        tool_input={"question": "anything"},
    )
    assert isinstance(out, str)
    assert ("no pinned" in out.lower()
            or "no bundle" in out.lower()
            or "ask the user" in out.lower())


def test_dispatch_finalize_turn_returns_none_as_signal():
    """finalize_turn is a control-flow signal; dispatcher returns None.
    The loop driver in agents.py intercepts it before reaching dispatch_tool_call,
    but if it ever does reach here, returning None is the contract."""
    out = dispatch_tool_call(
        config=MagicMock(),
        session={},
        tool_name="finalize_turn",
        tool_input={"action": "stay", "selection_updates": {}, "reply": "hi"},
    )
    assert out is None


def test_dispatch_unknown_tool_raises():
    import pytest
    with pytest.raises(ValueError, match="Unknown wizard builder tool"):
        dispatch_tool_call(
            config=MagicMock(), session={}, tool_name="does_not_exist", tool_input={},
        )


def test_refine_last_search_extracts_uids_from_plan_tool_result():
    """refine_last_search wraps _plan_tool_refine_last_search and returns uids + count."""
    session = {"results_history": [{"bundle_id": 1}],
               "nfcore_wizard": {"pinned_context": {"bundle_id": 1}}}
    config = MagicMock()
    with patch("chat_nextseek.agents._plan_tool_refine_last_search") as m:
        m.return_value = {
            "ok": True,
            "output": {"data": [{"uid": "U1"}, {"uid": "U2"}, {"uid": "U3"}], "count": 3},
        }
        out = dispatch_tool_call(
            config=config, session=session,
            tool_name="refine_last_search",
            tool_input={"filters": {"sampletype_code": "TIS"}},
        )
    assert out == {"uids": ["U1", "U2", "U3"], "count": 3}


def test_advanced_search_extracts_uids_from_plan_tool_result():
    """advanced_search wraps _plan_tool_new_search and returns uids + count."""
    session = {"results_history": [], "nfcore_wizard": {}}
    config = MagicMock()
    with patch("chat_nextseek.agents._plan_tool_new_search") as m:
        m.return_value = {
            "ok": True,
            "output": {"data": [{"uid": "FLY-001"}, {"uid": "FLY-002"}], "count": 2},
        }
        out = dispatch_tool_call(
            config=config, session=session,
            tool_name="advanced_search",
            tool_input={"query": "fly samples"},
        )
    assert out == {"uids": ["FLY-001", "FLY-002"], "count": 2}


def test_refine_last_search_preserves_count_when_data_empty(monkeypatch):
    """Critical-issue fix: if data is empty but output.count is non-zero,
    preserve the tool's reported count rather than overriding with len(uids)=0."""
    session = {"results_history": [{"bundle_id": 1}],
               "nfcore_wizard": {"pinned_context": {"bundle_id": 1}}}
    config = MagicMock()
    with patch("chat_nextseek.agents._plan_tool_refine_last_search") as m:
        m.return_value = {
            "ok": False,
            "output": {"data": [], "count": 200, "error": "API timeout"},
        }
        out = dispatch_tool_call(
            config=config, session=session,
            tool_name="refine_last_search",
            tool_input={"filters": {}},
        )
    assert out["uids"] == []
    assert out["count"] == 200   # preserved from underlying tool


def test_passthrough_to_parser_restores_wizard_state_on_exception():
    """If run_query raises, wizard state must be restored, not left as {}."""
    session = {
        "nfcore_wizard": {"active": True, "step": "builder",
                          "selection": {"uids": ["U1"]}},
    }
    config = MagicMock()
    saved_wizard = dict(session["nfcore_wizard"])

    def boom(*a, **kw):
        raise RuntimeError("simulated parser failure")

    with patch("chat_nextseek.orchestrator.run_query", side_effect=boom):
        out = dispatch_tool_call(
            config=config, session=session,
            tool_name="passthrough_to_parser",
            tool_input={"user_text": "what's NDMA?"},
        )

    # wizard state must be restored (NOT the temporary {} we set during call)
    assert session["nfcore_wizard"] == saved_wizard
    # output should be a human-readable error message
    assert isinstance(out, str)
    assert "couldn" in out.lower() or "error" in out.lower() or "simulated" in out.lower()
