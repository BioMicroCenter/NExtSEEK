"""tool_select_pipeline: evidence in, one small verdict out, never an exception."""
import json

import pytest

from chat_nextseek.pipeline import agent_tools, metadata_cache
from chat_nextseek.pipeline.sample_digest import DigestError
from chat_nextseek.pipeline.selection import Verdict
from chat_nextseek.pipeline.selection_context import PayloadTooLargeError

DIGEST = {"n_uids": 2, "metadata_summary": {}, "grouping_candidates": {}, "protocols": {}}


class _Config:
    LOG_DIR = "."

    def get_agent_model(self, key):
        return object(), "test-model", None


@pytest.fixture(autouse=True)
def _clean():
    metadata_cache.clear()
    yield
    metadata_cache.clear()


@pytest.fixture
def patched(monkeypatch):
    """Stub every boundary: the digest build, the payload build, and the model."""
    calls = {"digest": 0, "decide": 0}

    def fake_digest(config, uids, **kwargs):
        calls["digest"] += 1
        return DIGEST

    class _Ctx:
        def to_prompt_text(self, sections=None):
            return "PAYLOAD"

        def size_report(self, sections=None):
            return {"est_tokens": 1234}

    def fake_context(**kwargs):
        calls["context"] = kwargs
        return _Ctx()

    def fake_decide(**kwargs):
        calls["decide"] += 1
        calls["decide_kwargs"] = kwargs
        return Verdict(kind="chosen", pipelines=["rnaseq"], reason="bulk polyA")

    monkeypatch.setattr(agent_tools, "build_sample_digest", fake_digest)
    monkeypatch.setattr(agent_tools, "build_selection_context", fake_context)
    monkeypatch.setattr(agent_tools.selection, "decide", fake_decide)
    monkeypatch.setattr(agent_tools, "load_atlas",
                        lambda: {"pipelines": {"rnaseq": {"revision": "3.18.0"}}})
    return calls


def _call(tool_input, config=None, session=None, state=None, **kw):
    return json.loads(agent_tools.tool_select_pipeline(
        config or _Config(), session or {}, state if state is not None else {},
        tool_input, **kw))


def test_chosen_verdict_round_trips(patched):
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "expression?"})
    assert out["ok"] is True
    assert out["verdict"] == "chosen"
    assert out["pipelines"] == ["rnaseq"]
    assert out["reason"] == "bulk polyA"


def test_the_question_reaches_the_model_verbatim(patched):
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"],
           "question": "Which isoforms shift between groups?"})
    assert patched["decide_kwargs"]["question"] == "Which isoforms shift between groups?"


def test_only_the_three_live_sections_are_requested(patched):
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert patched["context"]["sections"] == ("atlas", "digest", "schemas")


def test_atlas_keys_are_passed_so_invented_keys_can_be_caught(patched):
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert patched["decide_kwargs"]["atlas_keys"] == {"rnaseq"}


def test_missing_question_is_a_tool_error_not_a_verdict():
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"]})
    assert out["ok"] is False
    assert "question" in out["error"]


def test_accessions_are_out_of_scope_with_a_reason():
    out = _call({"kind": "accessions", "accessions": ["SRR123"], "question": "q"})
    assert out["verdict"] == "out_of_scope"
    assert "accession" in out["reason"].lower()


def test_no_pinned_search_is_out_of_scope():
    out = _call({"kind": "last_search", "question": "q"}, session={})
    assert out["verdict"] == "out_of_scope"


def test_a_cohort_over_the_cap_is_out_of_scope_naming_the_cap(patched):
    uids = [f"D.SEQ-{i}" for i in range(agent_tools.MAX_SELECTION_UIDS + 1)]
    out = _call({"kind": "explicit_uids", "uids": uids, "question": "q"})
    assert out["verdict"] == "out_of_scope"
    assert str(agent_tools.MAX_SELECTION_UIDS) in out["reason"]
    assert patched["digest"] == 0          # and it never paid for the digest


def test_digest_error_is_out_of_scope(monkeypatch, patched):
    def boom(config, uids, **kwargs):
        raise DigestError("Sample metadata fetch failed: 403")
    monkeypatch.setattr(agent_tools, "build_sample_digest", boom)
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert out["verdict"] == "out_of_scope"
    assert "403" in out["reason"]


def test_payload_too_large_is_out_of_scope(monkeypatch, patched):
    def boom(**kwargs):
        raise PayloadTooLargeError("exceeds ceiling")
    monkeypatch.setattr(agent_tools, "build_selection_context", boom)
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert out["verdict"] == "out_of_scope"


def test_an_unexpected_exception_is_out_of_scope_not_a_crash(monkeypatch, patched):
    def boom(**kwargs):
        raise ZeroDivisionError("nope")
    monkeypatch.setattr(agent_tools, "build_selection_context", boom)
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert out["verdict"] == "out_of_scope"
    assert "ZeroDivisionError" in out["reason"]


def test_a_slow_digest_times_out_into_out_of_scope(monkeypatch, patched):
    import time

    def slow(config, uids, **kwargs):
        time.sleep(2)
        return DIGEST

    monkeypatch.setattr(agent_tools, "build_sample_digest", slow)
    monkeypatch.setattr(agent_tools, "DIGEST_TIMEOUT_SECONDS", 0.05)
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert out["verdict"] == "out_of_scope"
    assert "timed out" in out["reason"].lower()


def test_the_verdict_is_recorded_in_state(patched):
    state = {}
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"}, state=state)
    assert state["selection"]["verdict"] == "chosen"
    assert state["selection"]["pipelines"] == ["rnaseq"]


def test_selection_never_sets_pipeline_key(patched):
    """resolve_samples reads state['pipeline_key'] BEFORE tool_input. Writing it
    here would make a fork the user redirects resolve on the wrong pipeline."""
    state = {}
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"}, state=state)
    assert "pipeline_key" not in state


def test_progress_events_are_emitted_in_order(patched):
    seen = []
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"},
          send_event=lambda name, payload: seen.append(name))
    assert seen == ["selection_started", "selection_evidence_ready", "selection_done"]


def test_no_send_event_is_fine(patched):
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"},
                send_event=None)
    assert out["ok"] is True
