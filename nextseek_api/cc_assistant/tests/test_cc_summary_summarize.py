"""Hermetic: summarizer wrapper + actions-only fallback (no network)."""
import pytest

from nextseek_api.cc_assistant import cc_summary
from nextseek_api.cc_assistant.cc_config import CCMemoryConfig

types = pytest.importorskip("dmac_assistant.router.baml_client.types")


def _raw():
    return (
        '{"type":"user","message":{"role":"user","content":"make a plot"}}\n'
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"tool_use","name":"Bash","input":{"command":"python plot.py"}}]}}\n'
    ).encode("utf-8")


def _prov():
    return cc_summary.SummaryProvenance(
        chat_session_id="S1", claude_session_id="C1", transcript_path="/t.jsonl",
        chat_model="us.anthropic.claude-opus-4-8", generated_at="2026-06-29T00:00:00Z")


def test_fallback_builds_grounded_summary():
    cfg = CCMemoryConfig.from_env(source={})
    parsed = cc_summary.parse_transcript(_raw())
    summ = cc_summary.build_fallback_summary(parsed, _prov(), cfg)
    assert summ.writer == "fallback_actions"
    assert summ.summary_model == "none"
    assert summ.chat_session_id == "S1"
    assert summ.transcript_line_count == 2
    assert len(summ.items) >= 1
    assert all(ev.verified for it in summ.items for ev in it.evidence)


def test_summarize_uses_injected_fn_then_grounds():
    cfg = CCMemoryConfig.from_env(source={})

    def fake_fn(inp):
        return types.SessionSummary(
            chat_session_id="", claude_session_id=None, transcript_path="",
            transcript_line_count=0, turn_count=0, chat_model="", gist="made a plot",
            items=[types.MemoryItem(
                category=types.MemoryCategory.Artifact, statement="ran python plot.py",
                evidence=[types.EvidenceRef(
                line_start=2, line_end=2,
                quote=types.Checked(value="python plot.py", checks={}),
                verified=False,
            )],
                confidence=types.Confidence.High)],
            writer="", summary_model="", schema_version="1c/v1",
            generated_at="")

    summ = cc_summary.summarize_transcript(_raw(), _prov(), cfg, summarize_fn=fake_fn)
    assert summ.writer == "baml_gemini"
    assert summ.gist == "made a plot"
    assert summ.chat_session_id == "S1"
    assert summ.summary_model == cfg.summary_model
    assert summ.items[0].evidence[0].verified is True


def test_summarize_falls_back_on_summarizer_error():
    cfg = CCMemoryConfig.from_env(source={})

    def boom(inp):
        raise RuntimeError("gemini down")

    summ = cc_summary.summarize_transcript(_raw(), _prov(), cfg, summarize_fn=boom)
    assert summ.writer == "fallback_actions"
    assert summ.chat_session_id == "S1"
