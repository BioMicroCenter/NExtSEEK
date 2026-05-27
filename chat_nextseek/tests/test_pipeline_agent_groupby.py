"""Group-by resolution: tool dispatch + LLM loop + clarification handling."""
from unittest.mock import MagicMock, patch

from chat_nextseek.pipeline import agent as pipeline_agent, tools as pipeline_tools
from chat_nextseek.schemas.pipeline import FieldRef, GroupByResolution


# Tool-level tests ----------------------------------------------------------

def test_list_metadata_fields_groups_by_sample_type():
    bundle = {"by_sample_type": {
        "NHP": {"fields": {"Treatment1": {}, "Sex": {}, "Strain": {}}},
        "D.SEQ": {"fields": {"assay_name": {}, "read_length": {}}},
    }}
    out = pipeline_tools.list_metadata_fields(bundle, sample_types=[])
    assert "NHP" in out and "D.SEQ" in out
    assert set(out["NHP"]) == {"Treatment1", "Sex", "Strain"}
    assert set(out["D.SEQ"]) == {"assay_name", "read_length"}


def test_field_distribution_by_sample_type_returns_examples_and_counts():
    bundle = {"by_sample_type": {
        "NHP": {"fields": {"Treatment1": {"examples": ["NDMA", "vehicle"], "n_populated": 195}}},
    }}
    out = pipeline_tools.field_distribution_by_sample_type(bundle, field_name="Treatment1")
    assert "NHP" in out
    assert out["NHP"]["examples"] == ["NDMA", "vehicle"]
    assert out["NHP"]["n_populated"] == 195


def test_list_distinct_values_dedupes_and_caps():
    bundle = {"by_sample_type": {
        "NHP": {"fields": {"Treatment1": {
            "_all_values": ["NDMA"] * 100 + ["vehicle"] * 50,
        }}},
    }}
    out = pipeline_tools.list_distinct_values(
        bundle, sample_type="NHP", field_name="Treatment1",
    )
    assert sorted(out["values"]) == ["NDMA", "vehicle"]


# LLM-loop tests ------------------------------------------------------------

def _state_at_groupby_phase(group_by_phrase="exposure"):
    return {
        "active": True,
        "phase": "directive_parse",
        "directive": {
            "sub_mode": "build",
            "pipeline_key": "rnaseq",
            "samples_ref": {"kind": "last_search"},
            "group_by_phrase": group_by_phrase,
        },
        "resolution": {
            "source_uids": ["NHP-1", "NHP-2"],
            "leaves_filtered": [{"uid": "D.SEQ-1"}],
            "source_uids_with_no_leaves": [],
        },
        "sanity": {"verdict": "proceed", "leaves_to_use": ["D.SEQ-1"]},
        "metadata_bundle": {"data": []},
    }


def test_groupby_committed_advances_to_build():
    session = {"pipeline_agent": _state_at_groupby_phase()}
    with patch("chat_nextseek.pipeline.agent._pipeline_groupby_resolution") as gb, \
         patch("chat_nextseek.pipeline.agent._run_build_step",
               return_value={"action": "ask", "reply": "built", "params": None}):
        gb.return_value = GroupByResolution(
            field=FieldRef(sample_type="NHP", field_name="Treatment1"),
            distinct_values=["NDMA", "vehicle"],
            rationale="exposure → Treatment1",
        )
        out = pipeline_agent._run_groupby_or_build(session, MagicMock(), log_dir=None)
    assert out["reply"] == "built"
    assert session["pipeline_agent"]["groupby"]["field"]["field_name"] == "Treatment1"


def test_groupby_clarification_pauses_phase():
    session = {"pipeline_agent": _state_at_groupby_phase()}
    with patch("chat_nextseek.pipeline.agent._pipeline_groupby_resolution") as gb:
        gb.return_value = GroupByResolution(
            field=FieldRef(sample_type="NHP", field_name="Treatment1"),
            distinct_values=[],
            rationale="",
            requires_clarification=True,
            candidates=[
                FieldRef(sample_type="NHP", field_name="Treatment1"),
                FieldRef(sample_type="NHP", field_name="Treatment1Dose"),
            ],
            clarifying_question="Did you mean Treatment1 or Treatment1Dose?",
        )
        out = pipeline_agent._run_groupby_or_build(session, MagicMock(), log_dir=None)
    assert "Treatment1Dose" in out["reply"]
    assert session["pipeline_agent"]["phase"] == "awaiting_groupby_clarification"


def test_groupby_clarification_handler_re_resolves_with_hint():
    session = {"pipeline_agent": {
        **_state_at_groupby_phase(),
        "phase": "awaiting_groupby_clarification",
        "candidates_pending_user_pick": [
            {"sample_type": "NHP", "field_name": "Treatment1"},
            {"sample_type": "NHP", "field_name": "Treatment1Dose"},
        ],
    }}
    with patch("chat_nextseek.pipeline.agent._pipeline_groupby_resolution") as gb, \
         patch("chat_nextseek.pipeline.agent._run_build_step",
               return_value={"action": "ask", "reply": "built", "params": None}):
        gb.return_value = GroupByResolution(
            field=FieldRef(sample_type="NHP", field_name="Treatment1Dose"),
            distinct_values=["0", "50", "100"],
            rationale="user picked Treatment1Dose",
        )
        out = pipeline_agent.handle_turn(session, MagicMock(), "Treatment1Dose")
    assert out["reply"] == "built"
    gb_call_kwargs = gb.call_args.kwargs
    assert "Treatment1Dose" in str(gb_call_kwargs.get("user_hint", ""))


def test_groupby_skipped_when_phrase_is_null_single_cohort_build():
    session = {"pipeline_agent": {**_state_at_groupby_phase(group_by_phrase=None)}}
    with patch("chat_nextseek.pipeline.agent._pipeline_groupby_resolution") as gb, \
         patch("chat_nextseek.pipeline.agent._run_build_step",
               return_value={"action": "ask", "reply": "built", "params": None}):
        out = pipeline_agent._run_groupby_or_build(session, MagicMock(), log_dir=None)
    assert out["reply"] == "built"
    gb.assert_not_called()
