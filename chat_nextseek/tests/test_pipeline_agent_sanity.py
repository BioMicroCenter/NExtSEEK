"""Sanity LLM step + pipeline-switch handling."""
from unittest.mock import MagicMock, patch

from chat_nextseek import pipeline_agent
from chat_nextseek.schemas.pipeline import (
    DirectiveParseOutput, SamplesRef, SanityCheckOutput,
)


def _config():
    c = MagicMock()
    c.BASE_DIR = "/tmp"
    return c


def _resolve_ok(leaves=None):
    return {
        "source_uids": ["NHP-1", "NHP-2"],
        "accessions": [],
        "leaves_all": leaves or [{"uid": "D.SEQ-1", "assay": "RNA-seq", "source_uid": "NHP-1"}],
        "leaves_filtered": leaves or [{"uid": "D.SEQ-1", "assay": "RNA-seq", "source_uid": "NHP-1"}],
        "dropped_by_type_mismatch": [],
        "dropped_by_assay_mismatch": [],
        "source_uids_with_no_leaves": [],
        "metadata_bundle": {"data": []},
    }


def test_build_flow_proceed_advances_past_sanity():
    session = {}
    parsed = DirectiveParseOutput(
        sub_mode="build", pipeline_key="rnaseq",
        samples_ref=SamplesRef(kind="explicit_uids", uids=["NHP-1"]),
    )
    with patch("chat_nextseek.pipeline_agent._resolve_samples", return_value=_resolve_ok()), \
         patch("chat_nextseek.pipeline_agent._pipeline_sanity_check") as sanity, \
         patch("chat_nextseek.pipeline_agent._run_groupby_or_build",
               return_value={"action": "ask", "reply": "to groupby", "params": None}):
        sanity.return_value = SanityCheckOutput(
            verdict="proceed",
            leaves_to_use=["D.SEQ-1"],
            confidence_note="all match",
        )
        with patch("chat_nextseek.pipeline_agent._pipeline_directive_parse", return_value=parsed):
            out = pipeline_agent.start(session, _config(),
                                       user_query="run rnaseq on NHP-1",
                                       parser_plan={}, reporter_plan={})
    assert out["reply"] == "to groupby"
    assert session["pipeline_agent"]["sanity"]["verdict"] == "proceed"


def test_build_flow_mismatch_offers_alternative():
    session = {}
    parsed = DirectiveParseOutput(
        sub_mode="build", pipeline_key="sarek",
        samples_ref=SamplesRef(kind="explicit_uids", uids=["NHP-1"]),
    )
    with patch("chat_nextseek.pipeline_agent._resolve_samples", return_value=_resolve_ok()), \
         patch("chat_nextseek.pipeline_agent._pipeline_sanity_check") as sanity:
        sanity.return_value = SanityCheckOutput(
            verdict="mismatch",
            suggested_alternative_pipeline="rnaseq",
            confidence_note="all leaves are RNA-seq, not WGS",
        )
        with patch("chat_nextseek.pipeline_agent._pipeline_directive_parse", return_value=parsed):
            out = pipeline_agent.start(session, _config(),
                                       user_query="run sarek on NHP-1",
                                       parser_plan={}, reporter_plan={})
    assert out["action"] == "ask"
    assert "rnaseq" in out["reply"].lower()
    assert session["pipeline_agent"]["phase"] == "awaiting_pipeline_switch"


def test_pipeline_switch_handler_accepts_yes():
    session = {"pipeline_agent": {
        "active": True,
        "phase": "awaiting_pipeline_switch",
        "directive": {"sub_mode": "build", "pipeline_key": "sarek",
                      "samples_ref": {"kind": "explicit_uids", "uids": ["NHP-1"]}},
        "sanity": {"verdict": "mismatch", "suggested_alternative_pipeline": "rnaseq"},
    }}
    with patch("chat_nextseek.pipeline_agent._resolve_samples", return_value=_resolve_ok()), \
         patch("chat_nextseek.pipeline_agent._pipeline_sanity_check",
               return_value=SanityCheckOutput(verdict="proceed", leaves_to_use=["D.SEQ-1"])), \
         patch("chat_nextseek.pipeline_agent._run_groupby_or_build",
               return_value={"action": "ask", "reply": "rebuilt as rnaseq", "params": None}):
        out = pipeline_agent.handle_turn(session, _config(), "yes rnaseq")
    assert "rebuilt as rnaseq" in out["reply"]
    assert session["pipeline_agent"]["directive"]["pipeline_key"] == "rnaseq"


def test_pipeline_switch_handler_cancels_on_no():
    session = {"pipeline_agent": {
        "active": True,
        "phase": "awaiting_pipeline_switch",
        "directive": {},
        "sanity": {"suggested_alternative_pipeline": "rnaseq"},
    }}
    out = pipeline_agent.handle_turn(session, _config(), "no")
    assert out["action"] == "cancel"
    assert session["pipeline_agent"] == {}


def test_build_flow_resolution_error_replies_and_clears():
    session = {}
    parsed = DirectiveParseOutput(
        sub_mode="build", pipeline_key="rnaseq",
        samples_ref=SamplesRef(kind="last_search"),
    )
    with patch("chat_nextseek.pipeline_agent._resolve_samples",
               return_value={"error": "No pinned search to use."}), \
         patch("chat_nextseek.pipeline_agent._pipeline_directive_parse", return_value=parsed):
        out = pipeline_agent.start(session, _config(),
                                   user_query="run rnaseq from last search",
                                   parser_plan={}, reporter_plan={})
    assert out["action"] == "cancel"
    assert "no pinned" in out["reply"].lower()
    assert session.get("pipeline_agent") in (None, {})
