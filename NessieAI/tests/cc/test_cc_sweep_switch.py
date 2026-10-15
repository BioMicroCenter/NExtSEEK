"""Sweep kill switch (NEXTSEEK_CC_SUMMARY_SWEEP) and per-run cap (DMAC_CC_MEMORY_SWEEP_MAX_PER_RUN).

Fake metas and a fake summarizer: no model is called."""
import sys
import types

import pytest

from nextseek_api.cc_assistant import cc_sweep
from NessieAI.cc import cc_engine, cc_memory


def _meta(sid, updated):
    return cc_memory.SessionMeta(session_id=sid, updated_at=updated, fingerprint=None, summary=None,
                                 transcript_path=f"/{sid}.jsonl", changed=True)


@pytest.fixture
def sweep_env(monkeypatch):
    summarized = []

    class User:
        objects = type("O", (), {"all": staticmethod(lambda: [object()])})()

    auth = types.ModuleType("django.contrib.auth.models")
    auth.User = User
    monkeypatch.setitem(sys.modules, "django.contrib.auth.models", auth)
    tz = types.SimpleNamespace(now=lambda: types.SimpleNamespace(timestamp=lambda: 10_000.0, isoformat=lambda: "t"))
    monkeypatch.setitem(sys.modules, "django.utils.timezone", tz)
    mem = types.SimpleNamespace(sweep_idle_seconds=1)
    monkeypatch.setattr("NessieAI.cc.cc_config.CCPaths.from_env", staticmethod(lambda: object()))
    monkeypatch.setattr("NessieAI.cc.cc_config.CCMemoryConfig.from_env", staticmethod(lambda: mem))
    monkeypatch.setattr("NessieAI.cc.cc_session.read_store_transcript", lambda p: b"{}")
    monkeypatch.setattr(cc_engine, "transcript_is_verified_scrubbed", lambda p, raw: True)
    monkeypatch.setattr("NessieAI.router.router._resolve_cc_model_id", lambda: "m")
    monkeypatch.setattr("NessieAI.cc.cc_summary.fingerprint", lambda raw: "fp")

    def fake_summarize(raw, prov, cfg, summarize_fn=None):
        summarized.append(prov.chat_session_id)
        return types.SimpleNamespace(model_dump=lambda: {})

    monkeypatch.setattr("NessieAI.cc.cc_summary.summarize_transcript", fake_summarize)
    monkeypatch.setattr("NessieAI.cc.turn._persist_summary_standalone", lambda *a, **k: None)
    return summarized, monkeypatch


@pytest.mark.parametrize("off", ["0", "false", "off", "OFF"])
def test_the_switch_stops_the_sweep(monkeypatch, off):
    called = []
    monkeypatch.setenv("NEXTSEEK_CC_SUMMARY_SWEEP", off)
    monkeypatch.setattr(cc_sweep, "_run_sweep", lambda: called.append(1) or 3)
    fn = cc_sweep.sweep_cc_summaries
    assert getattr(fn, "__wrapped__", fn)() == 0
    assert called == []


def test_the_sweep_is_on_by_default(monkeypatch):
    monkeypatch.delenv("NEXTSEEK_CC_SUMMARY_SWEEP", raising=False)
    monkeypatch.setattr(cc_sweep, "_run_sweep", lambda: 3)
    fn = cc_sweep.sweep_cc_summaries
    assert getattr(fn, "__wrapped__", fn)() == 3


def test_a_run_summarizes_at_most_the_cap_oldest_first(sweep_env, monkeypatch):
    summarized, mp = sweep_env
    metas = [_meta(f"s{i}", i) for i in range(5, 0, -1)]  # s5 (updated 5) ... s1 (updated 1)
    mp.setattr("NessieAI.cc.turn._session_metas", lambda *a, **k: metas)
    monkeypatch.setenv("DMAC_CC_MEMORY_SWEEP_MAX_PER_RUN", "2")
    assert cc_sweep._run_sweep() == 2
    assert summarized == ["s1", "s2"]


def test_the_cap_defaults_to_twenty(sweep_env):
    summarized, mp = sweep_env
    mp.setattr("NessieAI.cc.turn._session_metas",
               lambda *a, **k: [_meta(f"s{i}", i) for i in range(25)])
    mp.delenv("DMAC_CC_MEMORY_SWEEP_MAX_PER_RUN", raising=False)
    assert cc_sweep._run_sweep() == 20
