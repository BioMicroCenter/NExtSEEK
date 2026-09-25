"""Hermetic: summarizer wrapper + actions-only fallback (no network)."""
import asyncio
import logging
import time

import pytest

from NessieAI.cc import cc_summary
from NessieAI.cc.cc_config import CCMemoryConfig

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


# ---------------------------------------------------------------------------- the time limit
#
# F7 (operator ruling 2026-09-25): the summary of another chat runs inside the turn before
# the agent container starts, so the turn's watchdog does not cover it. The BAML call gets
# 10 s; a stall past that takes the actions-only summary, as an error does. Every BAML
# call here is a fake; nothing reaches a model.


def _summary():
    return types.SessionSummary(
        chat_session_id="", claude_session_id=None, transcript_path="",
        transcript_line_count=0, turn_count=0, chat_model="", gist="made a plot",
        items=[], writer="", summary_model="", schema_version="1c/v1", generated_at="")


class _FakeB:
    """b.Summarize as the generated async client exposes it: sleep ``delay`` s, then answer."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = 0

    async def Summarize(self, input):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return _summary()


@pytest.fixture
def fake_b(monkeypatch):
    baml_client = pytest.importorskip("dmac_assistant.router.baml_client")

    def install(fake):
        monkeypatch.setattr(baml_client, "b", fake)
        return fake

    return install


def test_the_summary_call_has_a_ten_second_limit():
    assert cc_summary.SUMMARIZE_LIMIT_S == 10


def test_the_default_summarizer_still_answers_within_its_limit(fake_b):
    fake = fake_b(_FakeB())
    cfg = CCMemoryConfig.from_env(source={})

    summ = cc_summary.summarize_transcript(_raw(), _prov(), cfg)

    assert fake.calls == 1
    assert summ.writer == "baml_gemini" and summ.gist == "made a plot"


def test_a_stalled_summarizer_is_cut_at_its_limit_and_the_actions_summary_is_used(
        fake_b, monkeypatch, caplog):
    fake = fake_b(_FakeB(delay=5.0))
    monkeypatch.setattr(cc_summary, "SUMMARIZE_LIMIT_S", 0.2)
    cfg = CCMemoryConfig.from_env(source={})

    t0 = time.perf_counter()
    with caplog.at_level(logging.WARNING, logger=cc_summary.__name__):
        summ = cc_summary.summarize_transcript(_raw(), _prov(), cfg)

    assert time.perf_counter() - t0 < 2.0, "the turn waited out the stalled summarizer"
    assert fake.calls == 1
    assert summ.writer == "fallback_actions" and summ.summary_model == "none"
    assert summ.chat_session_id == "S1"
    assert "summarizer timed out after 0.2 s" in caplog.text


def test_the_sweep_gets_a_longer_limit_than_the_turn(monkeypatch):
    """The in-turn summary is cut at 10 s (F7); the Celery sweep, where no user waits and a
    stored fallback sticks until the transcript changes, gets its own longer limit."""
    seen = {}

    def fake_default(summarize_input, *, limit_s=None):
        seen["limit_s"] = limit_s
        return "ok"

    monkeypatch.setattr(cc_summary, "_default_summarize_fn", fake_default)
    assert cc_summary.sweep_summarize_fn(object()) == "ok"
    assert seen["limit_s"] == cc_summary.SWEEP_SUMMARIZE_LIMIT_S == 60
    assert cc_summary.SWEEP_SUMMARIZE_LIMIT_S > cc_summary.SUMMARIZE_LIMIT_S


def test_a_sweep_timeout_logs_the_sweep_limit(monkeypatch, caplog):
    def stalled(summarize_input):
        raise TimeoutError

    stalled.limit_s = 60
    cfg = CCMemoryConfig.from_env(source={})
    with caplog.at_level(logging.WARNING, logger=cc_summary.__name__):
        summ = cc_summary.summarize_transcript(_raw(), _prov(), cfg, summarize_fn=stalled)

    assert summ.writer == "fallback_actions"
    assert "summarizer timed out after 60 s" in caplog.text
